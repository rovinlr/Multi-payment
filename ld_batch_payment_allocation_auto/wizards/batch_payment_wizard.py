from odoo import api, fields, models

class BatchPaymentAllocationWizard(models.TransientModel):
    _name = 'batch.payment.allocation.wizard'
    _description = 'Batch Payment Allocation Wizard'

    partner_id = fields.Many2one('res.partner', string='Partner')
    partner_type = fields.Selection([('customer', 'Customer'), ('supplier', 'Supplier')], string='Partner Type')
    line_ids = fields.One2many('batch.payment.allocation.line', 'wizard_id', string='Invoices')

    @api.onchange('partner_id', 'partner_type')
    def _onchange_partner(self):
        if self.partner_id and self.partner_type:
            moves = self.env['account.move'].search([
                ('partner_id', '=', self.partner_id.id),
                ('move_type', 'in', ('out_invoice','in_invoice')),
                ('payment_state', '=', 'not_paid')
            ])
            self.line_ids = [(5, 0, 0)]
            lines = []
            for m in moves:
                lines.append((0,0,{
                    'move_id': m.id,
                    'amount_due': m.amount_residual,
                    'amount_to_pay': m.amount_residual,
                    'currency_id': m.currency_id.id,
                }))
            self.line_ids = lines

    def action_register_payment(self):
        return True

class BatchPaymentAllocationLine(models.TransientModel):
    _name = 'batch.payment.allocation.line'
    _description = 'Batch Payment Allocation Line'

    wizard_id = fields.Many2one('batch.payment.allocation.wizard')
    move_id = fields.Many2one('account.move', string='Invoice')
    amount_due = fields.Monetary(string='Amount Due', currency_field='currency_id')
    amount_to_pay = fields.Monetary(string='Amount to Pay', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string='Currency')
