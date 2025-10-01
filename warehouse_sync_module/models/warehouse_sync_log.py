from odoo import models, fields, api
from odoo.exceptions import UserError

# This model stores every warehouse sync event and alert in Odoo
class WarehouseSyncLog(models.Model):
    _name = 'warehouse.sync.log'                # Odoo technical name for the model
    _description = 'SyncVision Log'         # Display name in UI
    _order = 'create_date desc'                 # Default sort: newest logs first
      
    # --- BASIC FIELDS ---
    create_date = fields.Datetime('Date', readonly=True)   # When this log entry was created
    user_id = fields.Many2one('res.users', string='Triggered By', readonly=True)  # Who triggered the sync
    status = fields.Selection([
        ('success', 'Success'),
        ('fail', 'Fail')
    ], string='Status', readonly=True)        # Did the overall sync succeed or fail?
    message = fields.Text('Message', readonly=True)        # Details about the sync attempt
    
    # --- CUSTOM FIELDS FOR WAREHOUSE LOGIC ---
    sku = fields.Char('SKU')                                # Product SKU involved in this log entry
    main_id = fields.Char('External Main ID')               # External system's unique product ID
    brand = fields.Char("Brand")                            # name of all products
    barcode = fields.Char('Barcode')                        # Product barcode
    quantity = fields.Float('Quantity')                     # Stock quantity as synced
    alert = fields.Boolean('Alert (onhand < 5)')            # True if stock is low
    note = fields.Text('Note')                              # Message about stock state (e.g., 'LOW STOCK')
    
    # --- COMPUTED FIELDS ---
    product_id = fields.Many2one('product.product', string='Product', compute='_compute_product_id', store=True)
    product_name = fields.Char('Product Name', compute='_compute_product_id', store=True)
    sku_clickable = fields.Char('Clickable SKU', compute='_compute_sku_clickable')
    
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
    
    @api.depends('sku')
    def _compute_sku_clickable(self):
        """Create clickable SKU that opens product form"""
        for record in self:
            if record.sku:
                record.sku_clickable = record.sku
            else:
                record.sku_clickable = ''
    
    def action_open_product(self):
        """Open the related product form for editing"""
        self.ensure_one()
        if not self.product_id:
            raise UserError('No product found for this sync log entry.')
        
        return {
            'name': 'Product',
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

    # --- BUTTON: Allow user to trigger a manual sync from the UI ---
    @api.model
    def manual_sync(self):
        # Find a sync configuration (assumes only one config exists; adjust if needed)
        sync = self.env['warehouse.sync'].search([], limit=1)
        if not sync:
            # If no sync config found, show error to user
            raise UserError('No warehouse sync configuration found.')
        sync.run_sync()  # Trigger the sync process
        # Reload the page in the web UI so user sees updated logs/status
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }
