from odoo import models, fields, api
from odoo.exceptions import UserError

class WarehouseUnpublishedProducts(models.Model):
    _name = 'warehouse.unpublished.products'
    _description = 'Unpublished Products Log'
    _order = 'create_date desc'
    
    # Basic tracking fields
    create_date = fields.Datetime('Date Logged', readonly=True)
    sync_date = fields.Datetime('Sync Date', readonly=True)
    
    # Product identification fields
    sku = fields.Char('SKU', required=True)
    brand = fields.Char('Brand')
    barcode = fields.Char('Barcode')
    main_id = fields.Char('External ID')
    quantity = fields.Float('Warehouse Quantity')
    
    # Unpublished reason
    missing_fields = fields.Char('Missing Fields', help="Which fields are missing (SKU, Barcode, or both)")
    note = fields.Text('Note')
    
    # Computed fields for better UX
    product_id = fields.Many2one('product.product', string='Product', compute='_compute_product_id', store=True)
    product_name = fields.Char('Product Name', compute='_compute_product_id', store=True)
    
    @api.depends('sku', 'barcode')
    def _compute_product_id(self):
        """Find the related product based on SKU or barcode"""
        for record in self:
            product = None
            if record.sku:
                product = self.env['product.product'].search([('default_code', '=', record.sku)], limit=1)
            if not product and record.barcode:
                product = self.env['product.product'].search([('barcode', '=', record.barcode)], limit=1)
            
            record.product_id = product.id if product else False
            record.product_name = product.name if product else 'Product Not Found'
    
    def action_open_product(self):
        """Open the related product form for editing"""
        self.ensure_one()
        if not self.product_id:
            raise UserError('No product found for this unpublished product entry.')
        
        return {
            'name': f'Product: {self.product_name}',
            'type': 'ir.actions.act_window',
            'res_model': 'product.product',
            'res_id': self.product_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
    
    def action_open_product_by_sku(self):
        """Open product form by searching for SKU or barcode"""
        self.ensure_one()
        
        # Search for product by SKU first
        product = None
        if self.sku:
            product = self.env['product.product'].search([('default_code', '=', self.sku)], limit=1)
        
        # If not found by SKU, try barcode
        if not product and self.barcode:
            product = self.env['product.product'].search([('barcode', '=', self.barcode)], limit=1)
        
        if not product:
            raise UserError(f'No product found with SKU "{self.sku}" or barcode "{self.barcode}"')
        
        return {
            'name': f'Product: {product.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'product.product',
            'res_id': product.id,
            'view_mode': 'form',
            'target': 'current',
        }
