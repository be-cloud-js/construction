# -*- encoding: utf-8 -*-
##############################################################################
#
#    Copyright (c) 2015 be-cloud.be
#                       Jerome Sonnet <jerome.sonnet@be-cloud.be>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
import logging

from openerp import api, fields, models, _
from openerp.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

class BuildingAsset(models.Model):
    '''Building Asset'''
    _name = 'construction.building_asset'
    _description = 'Building Asset'

    _order = 'name'

    title = fields.Char(string="Title")

    name = fields.Char(string="Name", compute='_compute_name', store=True)

    active = fields.Boolean(string="Active", default=True)

    @api.one
    @api.depends('title','partner_id.name')
    def _compute_name(self):
        if self.partner_id and self.title:
            self.name = "%s - %s" % (self.title, self.partner_id.name)
        elif self.partner_id:
            self.name = self.partner_id.name
        else:
            self.name = self.title

    state = fields.Selection([
            ('development', 'In development'),
            ('onsale', 'On sale'),
            ('proposal', 'Proposal'),
            ('sold', 'Sold'),
        ], string='State', required=True, help="",default="development")

    type = fields.Selection([
            ('appartment', 'Appartment'),
            ('duplex', 'Duplex'),
            ('house', 'House'),
            ('contiguous', 'Contiguous House'),
            ('parking', 'Parking'),
        ], string='Type of asset', required=True, help="")

    address_id = fields.Many2one('res.partner', string='Asset address', domain="[('type', '=', 'delivery')]")

    partner_id = fields.Many2one('res.partner', string='Customer', ondelete='restrict', help="Customer for this asset.")

    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.user.company_id)

    confirmed_lead_id = fields.Many2one('crm.lead', string='Confirmed Lead')

    candidate_lead_ids = fields.One2many('crm.lead', 'building_asset_id', string='Candidate Leads', domain=['|',('active','=',True),('active','=',False)])
    
    analytic_account = fields.Char(string="BookIn Analytic")

    @api.onchange('confirmed_lead_id')
    def update_confirmed_lead_id(self):
        self.partner_id = self.confirmed_lead_id.partner_id
        self.state = 'sold'

    sale_order_ids = fields.One2many('sale.order', 'building_asset_id', string="Sale Orders", readonly=True)

    all_tags = fields.Many2many('sale.order.tag', string='SO', compute="_compute_tags")
    missing_tags = fields.Many2many('sale.order.tag', string='Missing SO', compute="_compute_tags")

    @api.one
    def _compute_tags(self):
        self.all_tags = self.sale_order_ids.filtered(lambda o: o.state == 'sale').mapped('construction_tag_ids')
        self.missing_tags = self.env['sale.order.tag'].search([]) - self.all_tags

    invoice_ids = fields.One2many('account.invoice','building_asset_id', string="Invoices", readonly=True)

class SaleOrder(models.Model):
    '''Sale Order'''
    _inherit = "sale.order"

    building_asset_id = fields.Many2one('construction.building_asset', string='Building Asset', ondelete='restrict')

    is_main_order = fields.Boolean('Main Order for this building asset')

    so_summary = fields.Text("SO Summary", compute="_compute_so_summary")

    active = fields.Boolean(default=True, help="If you uncheck the active field, it will disable the sale order without deleting it.")

    @api.one
    def _compute_so_summary(self):
        if not self.is_main_order:
            self.so_summary = ', '.join(self.order_line.mapped('name'))
        else :
            self.so_summary = 'voir détails...'

    @api.onchange('state')
    def update_asset_state(self):
        if self.state == 'sent':
            self.building_asset_id.state = 'proposal'
            # TODO add to the candidate lead_ids
        if self.state == 'sale' and self.is_main_order:
            self.building_asset_id.state = 'sold'
            self.confirmed_lead_id.id = self.opportunity_id.id
        if self.state == 'sale' and not self.is_main_order:
             for line in self.order_line:
                 line.qty_delivered = line.product_uom_qty

    @api.onchange('building_asset_id')
    def update_building_asset_id(self):
        if self.building_asset_id:
            self.company_id = self.building_asset_id.company_id

    @api.onchange('partner_id')
    def onchange_parter(self):
        if self.partner_id:
            if not self.building_asset_id :
                asset_id = self.env['construction.building_asset'].search([('partner_id','=',self.partner_id.id)])
                if asset_id :
                    self.building_asset_id = asset_id[0]

    @api.multi
    def _prepare_invoice(self):
        old_company_id = self.env.user.company_id
        self.env.user.company_id = self.company_id
        invoice_vals = super(SaleOrder, self)._prepare_invoice()
        invoice_vals['building_asset_id'] = self.building_asset_id.id or False
        self.env.user.company_id = old_company_id
        return invoice_vals

    amount_outstanding = fields.Monetary(string='Outstanding Amount', store=True, readonly=True, compute='_amount_outstanding')

    @api.depends('order_line.price_subtotal','order_line.qty_invoiced','order_line.product_uom_qty')
    def _amount_outstanding(self):
        """
        Compute the outstanding amounts of the SO.
        """
        for order in self:
            amount_outstanding = 0.0
            for line in order.order_line :
                amount_outstanding += line.price_subtotal * (line.product_uom_qty-line.qty_invoiced)
            order.update({
                'amount_outstanding': order.pricelist_id.currency_id.round(amount_outstanding),
            })

    construction_tag_ids = fields.Many2many('sale.order.tag', 'construction_sale_order_tag_rel', 'order_id', 'tag_id', string='Tags', copy=False)

class SaleOrderTag(models.Model):
    _name = 'sale.order.tag'
    _description = 'Sale Order Tags'
    name = fields.Char(string='Analytic Tag', index=True, required=True)
    color = fields.Integer('Color Index')
    active = fields.Boolean(default=True, help="Set active to false to hide the Sale Order Tag without removing it.")

    analytic_account = fields.Char(string="BookIn Analytic")

class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    building_asset_id = fields.Many2one('construction.building_asset', string='Building Asset', related="order_id.building_asset_id", store=True)

    qty_to_deliver = fields.Float(compute='_compute_qty_to_deliver',store=True, readonly=True)

    @api.depends('product_id', 'product_uom_qty', 'qty_delivered', 'state')
    def _compute_qty_to_deliver(self):
        """Compute the visibility of the inventory widget."""
        for line in self:
            line.qty_to_deliver = line.product_uom_qty - line.qty_delivered

    @api.multi
    def action_deliver_line(self):
        for order_line in self:
            order_line.write({'qty_delivered' : order_line.product_uom_qty})

class CrmLean(models.Model):
    '''CRM Lead'''
    _inherit = "crm.lead"

    building_asset_id = fields.Many2one('construction.building_asset', string='Building Asset', ondelete='restrict')

    @api.multi
    def _convert_opportunity_data(self, customer, team_id=False):
        res = super(CrmLean, self)._convert_opportunity_data(self, customer, team_id)
        res['building_asset_id'] = self.building_asset_id.id or False

class Invoice(models.Model):
    '''Invoice'''
    _inherit = 'account.invoice'

    building_asset_id = fields.Many2one('construction.building_asset', string='Building Asset', ondelete='restrict')

    first_line_tax_id = fields.Many2one('account.tax', string='Fist Line Tax', compute='_compute_first_line_tax_id')

    summary = fields.Text("Summary", compute="_compute_summary")

    @api.one
    def _compute_summary(self):
        self.summary = ', '.join(self.invoice_line_ids.mapped('name'))

    @api.multi
    def _compute_first_line_tax_id(self):
        for invoice in self:
            tax_id = False
            if len(invoice.invoice_line_ids) > 0 :
                line = invoice.invoice_line_ids[0]
                if len(line.invoice_line_tax_ids) > 0 :
                    tax_id = line.invoice_line_tax_ids[0]
            invoice.first_line_tax_id = tax_id

class Partner(models.Model):
    '''Partner'''
    _inherit = 'res.partner'

    matricule = fields.Char(string="Matricule")

    building_asset_ids = fields.One2many('construction.building_asset', 'partner_id', string='Building Assets')
    building_asset_count = fields.Integer(compute='_compute_building_asset_count', string='# of Assets')

    def _compute_building_asset_count(self):
        asset_data = self.env['construction.building_asset'].read_group(domain=[('partner_id', 'child_of', self.ids)],
                                                      fields=['partner_id'], groupby=['partner_id'])
        # read to keep the child/parent relation while aggregating the read_group result in the loop
        partner_child_ids = self.read(['child_ids'])
        mapped_data = dict([(m['partner_id'][0], m['partner_id_count']) for m in asset_data])
        for partner in self:
            # let's obtain the partner id and all its child ids from the read up there
            item = next(p for p in partner_child_ids if p['id'] == partner.id)
            partner_ids = [partner.id] + item.get('child_ids')
            # then we can sum for all the partner's child
            partner.building_asset_count = sum(mapped_data.get(child, 0) for child in partner_ids)
