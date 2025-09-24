# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class BatchPaymentAllocationWizard(models.TransientModel):
    _name = "batch.payment.allocation.wizard"
    _description = "Batch Payment Allocation (One payment -> Many invoices)"

    partner_type = fields.Selection([("customer","Customer"),("supplier","Vendor")], required=True, default="supplier")
    partner_id = fields.Many2one("res.partner", string="Partner", required=True, domain="[('parent_id','=',False)]")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True, readonly=True)
    journal_id = fields.Many2one("account.journal", string="Payment Journal", required=True, domain="[('type','in',('bank','cash'))]")
    payment_method_line_id = fields.Many2one("account.payment.method.line", string="Payment Method", domain="[('journal_id','=',journal_id)]")
    payment_date = fields.Date(default=fields.Date.context_today, required=True)
    payment_currency_id = fields.Many2one("res.currency", string="Payment Currency", required=True, default=lambda self: self.env.company.currency_id.id)
    communication = fields.Char(string="Memo / Reference")
    line_ids = fields.One2many("batch.payment.allocation.wizard.line", "wizard_id", string="Invoices")
    total_allocation = fields.Monetary(string="Total Allocation", currency_field="payment_currency_id", compute="_compute_total_allocation", store=False)

    @api.depends("line_ids.amount_to_pay")
    def _compute_total_allocation(self):
        for w in self:
            w.total_allocation = sum(w.line_ids.mapped("amount_to_pay"))

    @api.onchange("journal_id")
    def _onchange_journal_set_currency(self):
        if self.journal_id and self.journal_id.currency_id:
            self.payment_currency_id = self.journal_id.currency_id.id
        else:
            self.payment_currency_id = self.company_id.currency_id.id

    @api.onchange("partner_id","partner_type","payment_currency_id","payment_date","journal_id")
    def _onchange_partner(self):
        self._load_invoices()

    def _load_invoices(self):
        self.line_ids = [(5,0,0)]
        if not (self.partner_id and self.partner_type and self.payment_currency_id and self.payment_date):
            return
        inv_types = ["out_invoice"] if self.partner_type == "customer" else ["in_invoice"]
        domain = [
            ("commercial_partner_id","=",self.partner_id.commercial_partner_id.id),
            ("state","=","posted"),
            ("move_type","in",inv_types),
            ("payment_state","in",("not_paid","partial")),
            ("company_id","=",self.company_id.id),
        ]
        invoices = self.env["account.move"].search(domain, order="invoice_date asc, name asc", limit=300)
        lines = []
        for inv in invoices:
            residual_in_pay_cur = inv.currency_id._convert(inv.amount_residual, self.payment_currency_id, self.company_id, self.payment_date)
            lines.append((0,0,{"move_id": inv.id,
                               "invoice_date": inv.invoice_date,
                               "invoice_currency_id": inv.currency_id.id,
                               "invoice_amount_total": inv.amount_total,
                               "residual_in_payment_currency": residual_in_pay_cur,
                               "amount_to_pay": residual_in_pay_cur,
                               "currency_id": self.payment_currency_id.id}))
        self.line_ids = lines

    def _get_default_payment_method_line(self):
        if not self.journal_id:
            return False
        flow = "inbound" if self.partner_type == "customer" else "outbound"
        pml = self.journal_id._get_available_payment_method_lines(flow)
        return pml and pml[0] or False

    def action_confirm(self):
        self.ensure_one()
        if not self.line_ids or all(l.amount_to_pay <= 0.0 for l in self.line_ids):
            raise UserError(_("Set a positive amount to pay on at least one invoice."))
        if any(l.amount_to_pay < 0 for l in self.line_ids):
            raise ValidationError(_("Amounts must be >= 0."))
        if any(l.amount_to_pay - l.residual_in_payment_currency > 1e-6 for l in self.line_ids):
            raise ValidationError(_("You cannot allocate more than the residual on an invoice."))

        if not self.payment_method_line_id:
            self.payment_method_line_id = self._get_default_payment_method_line()
            if not self.payment_method_line_id:
                raise UserError(_("No payment method lines available on the selected journal."))

        total = sum(l.amount_to_pay for l in self.line_ids)
        if total <= 0:
            raise UserError(_("Total allocation must be > 0."))

        payment_vals = {
            "date": self.payment_date,
            "amount": total,
            "currency_id": self.payment_currency_id.id,
            "payment_type": "outbound" if self.partner_type == "supplier" else "inbound",
            "partner_type": self.partner_type,
            "partner_id": self.partner_id.id,
            "journal_id": self.journal_id.id,
            "payment_method_line_id": self.payment_method_line_id.id,
            "ref": self.communication or _("Batch payment for %s") % (self.partner_id.display_name),
        }
        payment = self.env["account.payment"].create(payment_vals)
        payment.action_post()

        pay_lines = payment.move_id.line_ids.filtered(lambda l: l.account_id.user_type_id.type in ("receivable","payable") and not l.reconciled)
        if not pay_lines:
            raise UserError(_("Could not find open receivable/payable line on payment."))
        pay_line = pay_lines[0]

        company = self.company_id
        for l in self.line_ids.filtered(lambda x: x.amount_to_pay > 0):
            inv = l.move_id
            inv_line = inv.line_ids.filtered(lambda ml: ml.account_id.user_type_id.type in ("receivable","payable") and not ml.reconciled)[:1]
            if not inv_line:
                continue
            inv_line = inv_line[0]
            amount_company = self.payment_currency_id._convert(l.amount_to_pay, company.currency_id, company, self.payment_date)
            debit_move = pay_line.id if pay_line.balance > 0 else inv_line.id
            credit_move = inv_line.id if pay_line.balance > 0 else pay_line.id
            self.env["account.partial.reconcile"].create({
                "debit_move_id": debit_move,
                "credit_move_id": credit_move,
                "amount": abs(amount_company),
                "company_currency_id": company.currency_id.id,
                "currency_id": self.payment_currency_id.id,
                "amount_currency": l.amount_to_pay,
            })

        return {
            "type": "ir.actions.act_window",
            "res_model": "account.payment",
            "view_mode": "form",
            "res_id": payment.id,
            "name": _("Batch Payment"),
        }

    def action_clear_lines(self):
        self.ensure_one()
        self.line_ids = [(5,0,0)]
        return {
            "type": "ir.actions.act_window",
            "res_model": "batch.payment.allocation.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "name": _("Batch Payment Allocation"),
        }


class BatchPaymentAllocationWizardLine(models.TransientModel):
    _name = "batch.payment.allocation.wizard.line"
    _description = "Batch Payment Allocation Line"

    wizard_id = fields.Many2one("batch.payment.allocation.wizard", ondelete="cascade")
    move_id = fields.Many2one("account.move", string="Invoice", required=True, readonly=True)
    invoice_date = fields.Date(string="Invoice Date", readonly=True)
    residual_in_payment_currency = fields.Monetary(string="Residual (Payment Currency)", currency_field="currency_id", readonly=True)
    amount_to_pay = fields.Monetary(string="Amount to Pay", currency_field="currency_id")
    currency_id = fields.Many2one("res.currency", string="Currency", required=True, readonly=True)
    invoice_currency_id = fields.Many2one("res.currency", string="Invoice Currency", readonly=True)
    invoice_amount_total = fields.Monetary(string="Invoice Total", currency_field="invoice_currency_id", readonly=True)

    @api.constrains("amount_to_pay")
    def _check_amount(self):
        for rec in self:
            if rec.amount_to_pay < 0:
                raise ValidationError(_("Amount to pay must be >= 0."))
            if rec.amount_to_pay - rec.residual_in_payment_currency > 1e-6:
                raise ValidationError(_("Amount to pay cannot exceed the residual."))
