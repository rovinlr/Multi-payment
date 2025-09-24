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
    payment_currency_id = fields.Many2one("res.currency", string="Payment Currency", required=True, default=lambda self: self.env.company.currency_id)
    communication = fields.Char(string="Memo / Reference")
    total_to_pay = fields.Monetary(string="Total to Pay", currency_field="payment_currency_id", compute="_compute_total_to_pay", store=False)
    line_ids = fields.One2many("batch.payment.allocation.wizard.line", "wizard_id", string="Invoices")

    @api.onchange("partner_type", "partner_id", "payment_currency_id")
    def _onchange_partner(self):
        for w in self:
            w._load_invoices()

    def _load_invoices(self):
        self.ensure_one()
        self.line_ids = [(5, 0, 0)]
        if not (self.partner_type and self.partner_id and self.payment_currency_id):
            return
        in_types = ("out_invoice","out_refund") if self.partner_type == "customer" else ("in_invoice","in_refund")
        moves = self.env["account.move"].search([
            ("move_type", "in", in_types),
            ("partner_id", "=", self.partner_id.id),
            ("state", "=", "posted"),
            ("payment_state", "in", ("not_paid", "partial")),
            ("company_id", "=", self.company_id.id),
        ], order="invoice_date asc, name asc")
        lines = []
        for mv in moves:
            residual_company = abs(mv.amount_residual)  # company currency
            if residual_company <= 0:
                continue
            # Convert residual to chosen payment currency
            residual_pay_cur = mv.company_currency_id._convert(
                residual_company, self.payment_currency_id, self.company_id, fields.Date.today()
            )
            lines.append((0, 0, {
                "move_id": mv.id,
                "name": mv.name,
                "invoice_date": mv.invoice_date,
                
                "residual_in_payment_currency": residual_pay_cur,
                "amount_to_pay": residual_pay_cur,
            }))
        self.line_ids = lines

    @api.depends("line_ids.amount_to_pay")
    def _compute_total_to_pay(self):
        for w in self:
            w.total_to_pay = sum(w.line_ids.mapped("amount_to_pay"))

    def _compute_payment_direction(self):
        # Returns ('inbound'|'outbound', partner_type)
        if self.partner_type == "customer":
            return "inbound", "customer"
        return "outbound", "supplier"

    def action_allocate(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("There are no invoice lines to pay."))
        if not self.journal_id:
            raise UserError(_("Please select a Payment Journal."))
        if not self.payment_method_line_id:
            # pick first available if not selected
            method = (self.journal_id.inbound_payment_method_line_ids if self.partner_type == "customer"
                      else self.journal_id.outbound_payment_method_line_ids)[:1]
            if not method:
                raise UserError(_("The selected journal has no compatible payment method."))
            self.payment_method_line_id = method.id

        # Only pay lines with positive amount_to_pay
        chosen = self.line_ids.filtered(lambda l: l.amount_to_pay and l.amount_to_pay > 0.0)
        if not chosen:
            raise UserError(_("Please set a positive Amount to Pay for at least one invoice."))

        # Registry context: selected invoices
        move_ids = chosen.mapped("move_id").ids
        payment_direction, partner_type = self._compute_payment_direction()

        # NOTE: In Odoo 18/19, account.payment.register handles grouped payments across multiple invoices.
        # It will allocate against selected invoices automatically. Fine-grained per-invoice allocation is
        # not exposed in the wizard API. We record a single payment for the sum and let reconciliation handle it.
        register_vals = {
            "payment_date": self.payment_date,
            "journal_id": self.journal_id.id,
            "payment_method_line_id": self.payment_method_line_id.id,
            
            "amount": sum(chosen.mapped("amount_to_pay")),
            "group_payment": True,
            "communication": self.communication or "",
        }

        reg = self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=move_ids
        ).create(register_vals)
        payments = reg._create_payments()  # creates and posts
        action = {
            "type": "ir.actions.act_window",
            "res_model": "account.payment",
            "view_mode": "tree,form",
            "domain": [("id", "in", payments.ids)],
            "name": _("Payments"),
        }
        return action


class BatchPaymentAllocationWizardLine(models.TransientModel):
    _name = "batch.payment.allocation.wizard.line"
    _description = "Batch Payment Allocation Line"

    wizard_id = fields.Many2one("batch.payment.allocation.wizard", required=True, ondelete="cascade")
    move_id = fields.Many2one("account.move", string="Invoice", required=True, domain="[('state','=','posted')]")
    name = fields.Char(string="Number", readonly=True)
    invoice_date = fields.Date(string="Invoice Date", readonly=True)
    residual_in_payment_currency = fields.Monetary(string="Residual (Payment Currency)", currency_field="currency_id", readonly=True)
    amount_to_pay = fields.Monetary(string="Amount to Pay", currency_field="currency_id")
    currency_id = fields.Many2one(related="wizard_id.payment_currency_id", string="Currency", store=False, readonly=True)

    @api.constrains("amount_to_pay")
    def _check_amount(self):
        for rec in self:
            if rec.amount_to_pay is None:
                continue
            if rec.amount_to_pay < 0:
                raise ValidationError(_("Amount to pay must be >= 0."))
            # Tolerance for rounding
            if rec.residual_in_payment_currency is not None and (rec.amount_to_pay - rec.residual_in_payment_currency) > 1e-6:
                raise ValidationError(_("Amount to pay cannot exceed the residual."))

    @api.onchange("move_id")
    def _onchange_move(self):
        for rec in self:
            rec.name = rec.move_id.name or ""
            rec.invoice_date = rec.move_id.invoice_date
