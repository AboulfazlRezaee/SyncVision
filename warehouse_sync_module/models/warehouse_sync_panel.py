from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class WarehouseSyncPanel(models.TransientModel):
    _name = "warehouse.sync.panel"
    _description = "SyncVision Control Panel"

    # Status fields to display current state
    last_sync_date = fields.Datetime("Last Sync", compute="_compute_sync_info", readonly=True)
    total_products_synced = fields.Integer("Total Products Synced", compute="_compute_sync_info", readonly=True)
    alerts_count = fields.Integer("Low Stock Alerts", compute="_compute_sync_info", readonly=True)
    missing_products_count = fields.Integer("Missing Products", compute="_compute_sync_info", readonly=True)
    unpublished_products_count = fields.Integer("Unpublished Products", compute="_compute_sync_info", readonly=True)
    high_stock_count = fields.Integer("High Stock (≥5)", compute="_compute_sync_info", readonly=True)
    sync_status = fields.Char("Status", compute="_compute_sync_info", readonly=True)
    
    # Configuration fields
    filter_missing_products = fields.Boolean("Filter Missing Products by SKU Prefix", default=False, 
                                            help="Enable to only show missing products with SKUs starting with: GN, PD, PB, PP, LVL, LP, PW")
    allowed_prefixes = fields.Char("Allowed SKU Prefixes", default="GN,PD,PB,PP,LVL,LP,PW", 
                                 help="Comma-separated list of SKU prefixes to filter missing products")
    
    # Email notification fields
    send_email_report = fields.Boolean("Send Email Report After Sync", default=True,
                                      help="Enable to send sync log report via email after each sync")
    recipient_email = fields.Char("Recipient Email", default="darkness.boogeyman85@gmail.com",
                                 help="Email address to send the sync report to")
    
    @api.depends()
    def _compute_sync_info(self):
        """Compute current sync status information"""
        for record in self:
            # Get latest sync log entry
            latest_log = self.env['warehouse.sync.log'].search([], order='create_date desc', limit=1)
            
            if latest_log:
                record.last_sync_date = latest_log.create_date
                record.sync_status = "Ready"
            else:
                record.last_sync_date = False
                record.sync_status = "No sync performed yet"
            
            # Count total synced products and alerts
            total_synced = self.env['warehouse.sync.log'].search_count([])
            alerts = self.env['warehouse.sync.log'].search_count([('alert', '=', True)])
            
            # Count missing products
            missing_products = self.env['warehouse.missing.products'].search_count([('status', '=', 'missing')])
            
            # Count unpublished products
            unpublished_products = self.env['warehouse.unpublished.products'].search_count([])
            
            # Count products with high stock (quantity >= 5) from sync log
            high_stock_products = self.env['warehouse.sync.log'].search_count([('alert', '=', False), ('quantity', '>=', 5)])
            
            record.total_products_synced = total_synced
            record.alerts_count = alerts
            record.missing_products_count = missing_products
            record.unpublished_products_count = unpublished_products
            record.high_stock_count = high_stock_products

    def action_manual_sync(self):
        """Execute manual warehouse sync"""
        try:
            # Create or get warehouse sync instance and run sync
            sync_obj = self.env['warehouse.sync']
            sync_obj.run_sync()
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sync Completed',
                    'message': f'SyncVision has been completed successfully! Total products synced: {self.total_products_synced}',
                    'type': 'success',
                    'sticky': True,
                }
            }
        except Exception as e:
            _logger.error("Manual sync failed: %s", str(e))
            raise UserError(f"Sync failed: {str(e)}")

    def action_view_sync_logs(self):
        """Open sync logs view"""
        return {
            'name': 'SyncVision Logs',
            'type': 'ir.actions.act_window',
            'res_model': 'warehouse.sync.log',
            'view_mode': 'list,form',
            'views': [
                (self.env.ref('warehouse_sync_module.view_warehouse_sync_log_list').id, 'list'),
                (self.env.ref('warehouse_sync_module.view_warehouse_sync_log_form').id, 'form')
            ],
            'target': 'current',
            'context': {'create': False},
        }

    def action_view_alerts(self):
        """Open low stock alerts view"""
        return {
            'name': 'Low Stock Alerts',
            'type': 'ir.actions.act_window',
            'res_model': 'warehouse.sync.log',
            'view_mode': 'list,form',
            'views': [
                (self.env.ref('warehouse_sync_module.view_warehouse_sync_log_list').id, 'list'),
                (self.env.ref('warehouse_sync_module.view_warehouse_sync_log_form').id, 'form')
            ],
            'domain': [('alert', '=', True)],
            'target': 'current',
        }
    
    def action_view_missing_products(self):
        """Open missing products view"""
        return {
            'name': 'Missing Products',
            'type': 'ir.actions.act_window',
            'res_model': 'warehouse.missing.products',
            'view_mode': 'list,form',
            'views': [
                (self.env.ref('warehouse_sync_module.view_warehouse_missing_products_list').id, 'list'),
                (self.env.ref('warehouse_sync_module.view_warehouse_missing_products_form').id, 'form')
            ],
            'domain': [('status', '=', 'missing')],
            'context': {'search_default_priority_brands': 1},
            'target': 'current',
        }
    
    def action_view_unpublished_products(self):
        """Open unpublished products view"""
        return {
            'name': 'Unpublished Products',
            'type': 'ir.actions.act_window',
            'res_model': 'warehouse.unpublished.products',
            'view_mode': 'list,form',
            'views': [
                (self.env.ref('warehouse_sync_module.view_warehouse_unpublished_products_list').id, 'list'),
                (self.env.ref('warehouse_sync_module.view_warehouse_unpublished_products_form').id, 'form')
            ],
            'target': 'current',
            'context': {'create': False},
        }
    
    def action_view_high_stock(self):
        """Open high stock products view"""
        return {
            'name': 'High Stock Products',
            'type': 'ir.actions.act_window',
            'res_model': 'warehouse.sync.log',
            'view_mode': 'list,form',
            'views': [
                (self.env.ref('warehouse_sync_module.view_warehouse_sync_log_list').id, 'list'),
                (self.env.ref('warehouse_sync_module.view_warehouse_sync_log_form').id, 'form')
            ],
            'domain': [('alert', '=', False), ('quantity', '>=', 5)],
            'target': 'current',
        }

    def action_refresh_panel(self):
        """Refresh panel data"""
        self._compute_sync_info()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Panel Refreshed',
                'message': 'Sync panel data has been refreshed.',
                'type': 'success',
                'sticky': False,
            }
        }

    def send_sync_report_email(self):
        """Send sync log report via email"""
        self.ensure_one()
        
        if not self.send_email_report or not self.recipient_email:
            return
        
        # Get recent sync logs 
        sync_logs = self.env['warehouse.sync.log'].search([], order='create_date desc')
        
        if not sync_logs:
            return
        
        # Prepare email content
        total_synced = len(sync_logs)
        alerts_count = len(sync_logs.filtered('alert'))
        high_stock_count = len(sync_logs.filtered(lambda x: not x.alert and x.quantity >= 5))
        
        # Get missing products count
        missing_count = self.env['warehouse.missing.products'].search_count([('status', '=', 'missing')])
        
        # Get unpublished products count
        unpublished_count = self.env['warehouse.unpublished.products'].search_count([])
        
        # Create HTML email body
        email_body = f"""
        <html>
        <body>
            <h2>SyncVision Report</h2>
            <p><strong>Sync completed at:</strong> {fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            
            <h3>Summary</h3>
            <ul>
                <li><strong>Total Products Synced: </strong> {total_synced}</li>
                <li><strong>Low Stock Alerts (< 5): </strong> {alerts_count}</li>
                <li><strong>High Stock Products (≥ 5): </strong> {high_stock_count}</li>
                <li><strong>Missing Products: </strong> {missing_count}</li>
                <li><strong>Unpublished Products: </strong> {unpublished_count}</li>
            </ul>
            
            <h3>Recent Sync Logs</h3>
            <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse;">
                <tr style="background-color: #f0f0f0;">
                    <th>SKU</th>
                    <th>Quantity</th>
                    <th>Alert</th>
                    <th>Note</th>
                    <th>Date</th>
                </tr>
        """
        
        # Add sync log entries to table
        for log in sync_logs:  # Show all entries
            alert_status = "⚠️ Low Stock" if log.alert else "✅ OK"
            email_body += f"""
                <tr>
                    <td>{log.sku or 'N/A'}</td>
                    <td>{log.quantity}</td>
                    <td>{alert_status}</td>
                    <td>{log.note or 'N/A'}</td>
                    <td>{log.create_date.strftime('%Y-%m-%d %H:%M') if log.create_date else 'N/A'}</td>
                </tr>
            """
        
        email_body += """
            </table>
            <p><em>This is an automated report from your SyncVision Module.</em></p>
        </body>
        </html>
        """
        
        # Send email
        try:
            mail_values = {
                'subject': f'SyncVision Report - {fields.Datetime.now().strftime("%Y-%m-%d %H:%M")}',
                'body_html': email_body,
                'email_to': self.recipient_email or 'darkness.boogeyman85@gmail.com',
                'email_from': self.env.user.email or 'cs@oskarme.com',
            }
            
            mail = self.env['mail.mail'].create(mail_values)
            mail.send()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'SyncVision Report',
                    'message': f'SyncVision report has been sent to {self.recipient_email}',
                    'type': 'success',
                    'sticky': False,
                }
            }
            
            # _logger.info(f"Sync report email sent to {self.recipient_email}")
            
        except Exception as e:
            _logger.error(f"Failed to send sync report email: {str(e)}")
