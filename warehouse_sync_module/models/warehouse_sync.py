#import Odoo and Python libraries
from odoo.exceptions import UserError # type: ignore
from odoo import models, fields, api # type: ignore
import requests
import logging
from datetime import timedelta

_logger = logging.getLogger(__name__)  # Standard Odoo logging

# --- Log model to track sync results and alerts ---
class WarehouseSyncLog(models.Model):
    _name = "warehouse.sync.log"
    _description = "SyncVision Alerts"

    # Basic info for log record
    main_id = fields.Char("External ID")        # External system's item number
    sku = fields.Char("SKU")                    # Stock Keeping Unit code
    brand = fields.Char("Brand")                # name of all products
    barcode = fields.Char("Barcode")            # Barcode (unique identifier)
    quantity = fields.Float("Quantity")         # Synced Odoo quantity
    alert = fields.Boolean("Alert (onhand < 5)")# True if low stock after sync
    note = fields.Text("Note")                  # Message about this sync event


# --- Add computed field to product.template for latest synced warehouse qty ---
class ProductTemplate(models.Model):
    _inherit = "product.template"

    # Brand stored on the product template so product creation can set it
    brand = fields.Char("Brand")
    main_warehouse_qty = fields.Float("Main Warehouse Quantity", compute="_compute_main_qty", store=True)

    @api.depends("default_code", "barcode")
    def _compute_main_qty(self):
        for product in self:
            # Look up latest sync log for this product (by SKU or barcode)
            sync = self.env["warehouse.sync.log"].search(
                ["|", ("sku", "=", product.default_code), ("barcode", "=", product.barcode)],
                limit=1, order="id desc"
            )
            product.main_warehouse_qty = sync.quantity if sync else 0.0


# --- Main Sync Utility ---
class WarehouseSync(models.Model):
    _name = "warehouse.sync"
    _description = "Warehouse Stock Sync Utility"

    def _sanitize_identifier(self, value):
        if value is None:
            return ""
        value_str = str(value).strip()
        if not value_str:
            return ""
        lowered = value_str.lower()
        if lowered in {"none", "null", "n/a", "na"}:
            return ""
        return value_str

    def _normalize_identifier(self, value):
        if not value:
            return None
        value_str = str(value).strip()
        if not value_str or value_str.lower() == "none":
            return None
        normalized = "".join(ch for ch in value_str if ch.isalnum())
        return normalized.upper() if normalized else None

    def _is_stockable_product(self, product):
        if not product:
            return False
        detailed_type = getattr(product, 'detailed_type', False)
        if detailed_type:
            return detailed_type == 'product'
        product_type = getattr(product, 'type', False)
        return product_type == 'product'

    @api.model
    def run_sync(self):    
        """
        This method is the entry point for running the sync.
        It clears old logs, calls sync_products_chunked(), logs the result, and sends email report.
        """
        try:
            # Clear all previous sync logs before starting new sync
            self._clear_previous_logs()
            
            # Run chunked sync to prevent timeouts
            self.sync_products_chunked()
        
            message = "Sync successful"
            status = 'success'
        except Exception as e:
            message = str(e)
            status = 'fail'
            _logger.error("Sync failed: %s", str(e))
        
        # Log the overall sync result
        self.env['warehouse.sync.log'].create({
            'sku': 'SYSTEM',
            'brand': 'SyncVision',
            'main_id': None,
            'barcode': None,
            'quantity': 0,
            'alert': status == 'fail',
            'note': f"Sync {status}: {message}"
        })
        
        # Send email report after sync completion
        self._send_email_report_if_enabled()
    
    def sync_products_chunked(self, chunk_size=100):
        """
        Process products in chunks to prevent connection timeouts and memory issues.
        This is the main method that prevents 'connection lost' errors.
        For very large inventories (8000+ products), use smaller chunk sizes (25-50).
        """
        _logger.info(f"Starting chunked sync process with chunk size: {chunk_size}...")
        
        # First, get API data
        api_data = self._fetch_api_data()
        if not api_data:
            _logger.warning("No API data received, sync aborted")
            return
        
        # Get all products from Odoo
        all_products = self.env["product.product"].search([('type', 'in', ['product', 'consu'])])
        total_products = len(all_products)
        
        # Auto-adjust chunk size for very large inventories
        if total_products > 8000:
            chunk_size = min(chunk_size, 80)  # Use very small chunks for huge inventories
            _logger.info(f"Huge inventory detected ({total_products} products). Using very small chunk size: {chunk_size}")
        elif total_products > 5000:
            chunk_size = min(chunk_size, 100)  # Use smaller chunks for large inventories
            _logger.info(f"Large inventory detected ({total_products} products). Using smaller chunk size: {chunk_size}")
        
        _logger.info(f"Processing {total_products} products in chunks of {chunk_size}")
        
        processed = 0
        failed = 0
        processed_skus = set()  # Track which API products were found in Odoo
        
        # Process products in chunks
        for i in range(0, total_products, chunk_size):
            chunk = all_products[i:i + chunk_size]
            chunk_num = (i // chunk_size) + 1
            total_chunks = (total_products + chunk_size - 1) // chunk_size
            
            _logger.info(f"Processing chunk {chunk_num}/{total_chunks} ({len(chunk)} products)")
            
            try:
                # Process this chunk and collect processed SKUs
                chunk_processed, chunk_processed_skus = self._process_product_chunk(chunk, api_data)
                processed += chunk_processed
                processed_skus.update(chunk_processed_skus)
                
                # Commit after each chunk to save progress and prevent timeouts
                self.env.cr.commit()
                
                _logger.info(f"Chunk {chunk_num} completed. Progress: {processed}/{total_products}")
                
                # For very large inventories, add extra delay and memory cleanup
                import time
                if total_products > 8000:
                    time.sleep(2.0)  # 2 second delay for huge inventories
                    # Force garbage collection for memory cleanup
                    import gc
                    gc.collect()
                else:
                    time.sleep(1.0)  # 1 second delay for normal large inventories
                
            except Exception as e:
                failed += len(chunk)
                _logger.error(f"Error in chunk {chunk_num}: {str(e)}")
                # Continue with next chunk
                continue
        
        # After processing all chunks, check for missing products
        self._process_missing_products(api_data, processed_skus)
        
        _logger.info(f"Chunked sync completed. Processed: {processed}, Failed: {failed}, Total: {total_products}")
    
    def _fetch_api_data(self):
        """Fetch data from API and return processed data structure"""
        url = "Your_API_Endpoint_Here"  # Replace with actual API endpoint
        
        try:
            _logger.info(f"Fetching API data from: {url}")
            response = requests.get(url, timeout=30)  # 30 second timeout
            _logger.info(f"API response status: {response.status_code}")
            
            payload = response.json()
            
            if not payload.get("success") or "data" not in payload:
                _logger.error(f"Invalid API response: {payload}")
                return None
            
            # Process API data into lookup dictionaries
            api_products_by_sku = {}
            api_products_by_barcode = {}
            
            for item in payload["data"]:
                sku = self._sanitize_identifier(item.get("sku"))
                barcode = self._sanitize_identifier(item.get("barcode"))
                
                entry = {
                    'ext_id': self._sanitize_identifier(item.get("itemNumber")),
                    'qty': float(item.get("southbayStock") or 0.0),
                    'sku': sku,
                    'barcode': barcode if barcode and barcode.lower() != 'none' else None,
                    'brand': self._sanitize_identifier(item.get("brand")),
                }
                
                normalized_sku = self._normalize_identifier(sku)
                if normalized_sku:
                    entry['normalized_sku'] = normalized_sku
                    api_products_by_sku[normalized_sku] = entry
                
                normalized_barcode = self._normalize_identifier(barcode)
                if normalized_barcode:
                    api_products_by_barcode[normalized_barcode] = entry
            
            _logger.info(f"Processed {len(payload['data'])} API products")
            return {'by_sku': api_products_by_sku, 'by_barcode': api_products_by_barcode}
            
        except requests.Timeout:
            _logger.error("API request timed out")
            return None
        except Exception as e:
            _logger.error(f"Error fetching API data: {str(e)}")
            return None
    
    def _process_product_chunk(self, products, api_data):
        """Process a chunk of products and return count of processed items and processed SKUs"""
        processed_count = 0
        processed_skus = set()
        
        api_products_by_sku = api_data['by_sku']
        api_products_by_barcode = api_data['by_barcode']
        
        for product in products:
            try:
                # Process individual product and get processed SKU
                processed_sku = self._process_single_product(product, api_products_by_sku, api_products_by_barcode)
                if processed_sku:
                    processed_skus.add(processed_sku)
                processed_count += 1
                
            except Exception as e:
                _logger.error(f"Error processing product {product.id}: {str(e)}")
                continue
        
        return processed_count, processed_skus
    
    def _process_single_product(self, product, api_products_by_sku, api_products_by_barcode):
        """Process a single product - extracted from the main sync logic. Returns normalized_sku if found in API."""
        # Check for valid SKU and barcode
        raw_sku = product.default_code
        raw_barcode = product.barcode
        
        has_sku = bool(raw_sku and str(raw_sku).strip() and str(raw_sku).strip().lower() not in ['none', 'null', 'n/a', 'na', ''])
        has_barcode = bool(raw_barcode and str(raw_barcode).strip() and str(raw_barcode).strip().lower() not in ['none', 'null', 'n/a', 'na', ''])
        
        # Normalize identifiers for API lookup
        sku = self._sanitize_identifier(raw_sku)
        barcode = self._sanitize_identifier(raw_barcode)
        normalized_sku = self._normalize_identifier(sku)
        normalized_barcode = self._normalize_identifier(barcode)
        
        should_process = has_sku or has_barcode
        is_stockable = self._is_stockable_product(product)
        
        # Look up this product in API data
        api_data = None
        processed_sku = None
        if normalized_sku and normalized_sku in api_products_by_sku:
            api_data = api_products_by_sku[normalized_sku]
            processed_sku = normalized_sku
        if not api_data and normalized_barcode and normalized_barcode in api_products_by_barcode:
            api_data = api_products_by_barcode[normalized_barcode]
            # Get the normalized_sku from the API data if found by barcode
            if api_data and api_data.get('normalized_sku'):
                processed_sku = api_data['normalized_sku']
        
        if api_data:
            qty = api_data['qty']
            ext_id = api_data['ext_id']
            api_brand = api_data.get('brand')
        else:
            qty = 0.0
            ext_id = None
            api_brand = None
        
        # Apply stock rules
        if qty == 0:
            new_qty = 0
        elif 30 <= qty <= 50:
            new_qty = 2
        elif qty < 30:
            new_qty = 0
        elif 50 < qty < 200:
            new_qty = 5
        elif qty >= 200:
            new_qty = 10
        else:
            new_qty = product.qty_available
        
        odoo_qty = product.qty_available
        alert = new_qty < 5
        
        # Create log entry
        log_sku = sku if sku else f"NO_SKU_{product.id}"
        log_barcode = barcode if barcode and barcode.lower() != 'none' else None
        log_ext_id = ext_id if ext_id else None
        
        if not should_process:
            log_note = "SKIPPED: missing SKU and barcode"
        else:
            if not api_data:
                log_note = "No API match; treated as zero inventory"
            elif alert:
                log_note = "LOW STOCK"
            elif new_qty != odoo_qty:
                log_note = f"Stock updated: {odoo_qty} → {new_qty}"
            else:
                log_note = "Stock OK"
            
            if api_data and not (has_sku and has_barcode):
                missing_parts = []
                if not has_sku:
                    missing_parts.append("SKU")
                if not has_barcode:
                    missing_parts.append("barcode")
                if missing_parts:
                    log_note += f" (missing {' and '.join(missing_parts)} - UNPUBLISHED)"
        
        # Create sync log
        self.env["warehouse.sync.log"].create({
            "sku": log_sku,
            "brand": api_brand,
            "main_id": log_ext_id,
            "barcode": log_barcode,
            "quantity": new_qty,
            "alert": alert,
            "note": log_note
        })
        
        # Update stock if needed
        if should_process and is_stockable and new_qty != odoo_qty:
            self._update_product_stock(product, new_qty)
        
        # Handle product publishing
        self._handle_product_publishing(product, has_sku, has_barcode, api_data, qty)
        
        # Return the processed SKU if this product was found in API
        return processed_sku
    
    def _update_product_stock(self, product, new_qty):
        """Update product stock quantity"""
        location = self.env.ref("stock.stock_location_stock")
        
        quant = self.env['stock.quant'].sudo().search([
            ('product_id', '=', product.id),
            ('location_id', '=', location.id)
        ], limit=1)
        
        if quant:
            quant.sudo().write({'inventory_quantity': new_qty})
            quant.sudo().action_apply_inventory()
        else:
            new_quant = self.env['stock.quant'].sudo().create({
                'product_id': product.id,
                'location_id': location.id,
                'inventory_quantity': new_qty,
                'quantity': 0
            })
            new_quant.sudo().action_apply_inventory()
    
    def _handle_product_publishing(self, product, has_sku, has_barcode, api_data, qty):
        """Handle product publishing and unpublished products logging"""
        template = product.product_tmpl_id
        if not template:
            return
        
        publish = bool(has_sku and has_barcode)
        template.sudo().write({'website_published': publish})
        
        # Log unpublished products
        if not publish:
            missing_parts = []
            unpublish_reason = "Unknown reason"
            
            if not has_sku and not has_barcode:
                missing_parts = ["SKU", "barcode"]
                unpublish_reason = "Missing both SKU and barcode"
            elif not has_sku:
                missing_parts = ["SKU"]
                unpublish_reason = "Missing SKU"
            elif not has_barcode:
                missing_parts = ["barcode"]
                unpublish_reason = "Missing barcode"
            
            self.env["warehouse.unpublished.products"].create({
                "sku": product.default_code if product.default_code else f"NO_SKU_{product.id}",
                "brand": api_data.get('brand') if api_data else (product.product_tmpl_id.brand or "Unknown"),
                "main_id": api_data.get('ext_id') if api_data else None,
                "barcode": product.barcode if product.barcode and product.barcode.lower() != 'none' else None,
                "quantity": qty if api_data else 0.0,
                "missing_fields": " and ".join(missing_parts) if missing_parts else "Other",
                "sync_date": fields.Datetime.now(),
                "note": f"Product unpublished: {unpublish_reason}. " + 
                       (f"API quantity: {qty}" if api_data else "No API data found")
            })
    
    def _process_missing_products(self, api_data, processed_skus):
        """Check for API products that were NOT found in Odoo inventory and create missing product records"""
        try:
            # Get configuration from warehouse sync panel
            panel_config = self.env['warehouse.sync.panel'].search([], limit=1)
            filter_enabled = panel_config.filter_missing_products if panel_config else False
            
            # Parse allowed prefixes from configuration
            allowed_prefixes = []
            if filter_enabled and panel_config and panel_config.allowed_prefixes:
                allowed_prefixes = [prefix.strip() for prefix in panel_config.allowed_prefixes.split(',') if prefix.strip()]
            
            # Use the existing API data instead of making another API call
            api_products_by_sku = api_data['by_sku']
            
            _logger.info(f"Checking for missing products from {len(api_products_by_sku)} API products")
            _logger.info(f"Filter enabled: {filter_enabled}, Allowed prefixes: {allowed_prefixes}")
            _logger.info(f"Processed SKUs count: {len(processed_skus)}")
            
            missing_count = 0
            skipped_by_filter = 0
            
            # Check each API product to see if it was processed (found in Odoo)
            for normalized_sku, product_data in api_products_by_sku.items():
                # Skip if this product was found in Odoo inventory
                if normalized_sku in processed_skus:
                    continue
                
                sku = product_data.get('sku')
                brand = product_data.get('brand')
                ext_id = product_data.get('ext_id')
                barcode = product_data.get('barcode')
                qty = product_data.get('qty', 0.0)
                
                # Apply prefix filtering if enabled
                if filter_enabled and allowed_prefixes:
                    if not (sku and any(sku.startswith(prefix) for prefix in allowed_prefixes)):
                        skipped_by_filter += 1
                        continue  # Skip products that don't match allowed prefixes
                
                # Check if this missing product already exists (avoid duplicates)
                existing = self.env["warehouse.missing.products"].search([
                    ('sku', '=', sku.strip() if sku else f"UNKNOWN_SKU_{normalized_sku}"),
                    ('status', '=', 'missing')
                ], limit=1)
                
                if existing:
                    # Update existing record with latest sync date and quantity
                    existing.write({
                        'sync_date': fields.Datetime.now(),
                        'quantity': qty,
                        'note': f"Product still missing from Odoo inventory. Latest API quantity: {qty}"
                    })
                else:
                    # Create new missing product record
                    self.env["warehouse.missing.products"].create({
                        "sku": sku.strip() if sku else f"UNKNOWN_SKU_{normalized_sku}",
                        "brand": brand if brand and brand.lower() != 'none' else None,
                        "main_id": ext_id if ext_id else None,
                        "barcode": barcode if barcode and barcode.lower() != 'none' else None,
                        "quantity": qty,
                        "sync_date": fields.Datetime.now(),
                        "note": f"Product found in API but missing from Odoo inventory. API quantity: {qty}"
                    })
                    missing_count += 1
            
            _logger.info(f"Missing products processing complete:")
            _logger.info(f"  - New missing product records created: {missing_count}")
            _logger.info(f"  - Products skipped by prefix filter: {skipped_by_filter}")
            _logger.info(f"  - Total API products not found in Odoo: {len(api_products_by_sku) - len(processed_skus)}")
            
        except Exception as e:
            _logger.error(f"Error processing missing products: {str(e)}")
    
    def test_missing_products_functionality(self):
        """Test method to verify missing products functionality is working"""
        try:
            _logger.info("Testing missing products functionality...")
            
            # Get API data
            api_data = self._fetch_api_data()
            if not api_data:
                _logger.error("Could not fetch API data for testing")
                return False
            
            # Create a mock processed_skus set (empty to simulate all products as missing)
            processed_skus = set()
            
            # Process missing products with empty processed_skus
            self._process_missing_products(api_data, processed_skus)
            
            # Check if missing products were created
            missing_count = self.env['warehouse.missing.products'].search_count([('status', '=', 'missing')])
            _logger.info(f"Test completed. Found {missing_count} missing products")
            
            return missing_count > 0
            
        except Exception as e:
            _logger.error(f"Error testing missing products functionality: {str(e)}")
            return False

    def debug_missing_products_issue(self):
        """Debug method to investigate missing products issue"""
        try:
            _logger.info("=== DEBUGGING MISSING PRODUCTS ISSUE ===")
            
            # 1. Check panel configuration
            panel_config = self.env['warehouse.sync.panel'].search([], limit=1)
            if panel_config:
                _logger.info(f"Panel config found - Filter enabled: {panel_config.filter_missing_products}")
                _logger.info(f"Allowed prefixes: {panel_config.allowed_prefixes}")
            else:
                _logger.info("No panel configuration found")
            
            # 2. Get API data
            api_data = self._fetch_api_data()
            if not api_data:
                _logger.error("Could not fetch API data")
                return False
            
            api_products_by_sku = api_data['by_sku']
            _logger.info(f"API returned {len(api_products_by_sku)} products")
            
            # 3. Show sample API products
            sample_count = 0
            for normalized_sku, product_data in api_products_by_sku.items():
                if sample_count < 5:  # Show first 5
                    _logger.info(f"API Product {sample_count + 1}: SKU='{product_data.get('sku')}', Brand='{product_data.get('brand')}', Qty={product_data.get('qty')}")
                    sample_count += 1
            
            # 4. Get all Odoo products and simulate processed_skus
            all_products = self.env["product.product"].search([('type', 'in', ['product', 'consu'])])
            _logger.info(f"Found {len(all_products)} products in Odoo")
            
            # 5. Simulate the processed_skus logic
            processed_skus = set()
            for product in all_products[:10]:  # Check first 10 products
                raw_sku = product.default_code
                sku = self._sanitize_identifier(raw_sku)
                normalized_sku = self._normalize_identifier(sku)
                
                if normalized_sku and normalized_sku in api_products_by_sku:
                    processed_skus.add(normalized_sku)
                    _logger.info(f"Product {product.id} matched API: SKU='{raw_sku}' -> normalized='{normalized_sku}'")
            
            _logger.info(f"Processed SKUs count: {len(processed_skus)}")
            
            # 6. Check how many API products would be considered missing
            missing_candidates = 0
            filter_enabled = panel_config.filter_missing_products if panel_config else False
            allowed_prefixes = []
            if filter_enabled and panel_config and panel_config.allowed_prefixes:
                allowed_prefixes = [prefix.strip() for prefix in panel_config.allowed_prefixes.split(',') if prefix.strip()]
            
            for normalized_sku, product_data in api_products_by_sku.items():
                if normalized_sku in processed_skus:
                    continue  # Skip processed products
                
                sku = product_data.get('sku')
                
                # Apply prefix filtering if enabled
                if filter_enabled and allowed_prefixes:
                    if not (sku and any(sku.startswith(prefix) for prefix in allowed_prefixes)):
                        continue  # Skip products that don't match allowed prefixes
                
                missing_candidates += 1
                if missing_candidates <= 5:  # Show first 5 missing candidates
                    _logger.info(f"Missing candidate {missing_candidates}: SKU='{sku}', Brand='{product_data.get('brand')}', Qty={product_data.get('qty')}")
            
            _logger.info(f"Total missing candidates: {missing_candidates}")
            
            # 7. Check existing missing products records
            existing_missing = self.env['warehouse.missing.products'].search_count([])
            existing_missing_status = self.env['warehouse.missing.products'].search_count([('status', '=', 'missing')])
            _logger.info(f"Existing missing products records: {existing_missing} (status=missing: {existing_missing_status})")
            
            _logger.info("=== DEBUG COMPLETE ===")
            return True
            
        except Exception as e:
            _logger.error(f"Error in debug method: {str(e)}")
            return False

    def test_missing_products_without_clearing(self):
        """Test missing products functionality without clearing existing records first"""
        try:
            _logger.info("Testing missing products functionality (without clearing)...")
            
            # Get API data
            api_data = self._fetch_api_data()
            if not api_data:
                _logger.error("Could not fetch API data for testing")
                return False
            
            # Create a mock processed_skus set with only a few items to simulate many missing products
            processed_skus = set()
            
            # Add just a couple of fake processed SKUs to simulate some products being found
            api_products_by_sku = api_data['by_sku']
            count = 0
            for normalized_sku in api_products_by_sku.keys():
                if count < 2:  # Only mark first 2 as processed
                    processed_skus.add(normalized_sku)
                    count += 1
                else:
                    break
            
            _logger.info(f"Simulating {len(processed_skus)} processed SKUs out of {len(api_products_by_sku)} API products")
            
            # Process missing products
            self._process_missing_products(api_data, processed_skus)
            
            # Check if missing products were created
            missing_count = self.env['warehouse.missing.products'].search_count([('status', '=', 'missing')])
            _logger.info(f"Test completed. Found {missing_count} missing products")
            
            return missing_count > 0
            
        except Exception as e:
            _logger.error(f"Error testing missing products functionality: {str(e)}")
            return False

    def clear_all_missing_products(self):
        """Manually clear all missing products records - use with caution"""
        try:
            missing_products = self.env['warehouse.missing.products'].search([])
            count = len(missing_products)
            if missing_products:
                missing_products.unlink()
                _logger.info(f"Manually cleared {count} missing product records")
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Missing Products Cleared',
                        'message': f'Successfully cleared {count} missing product records.',
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                _logger.info("No missing products to clear")
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'No Missing Products',
                        'message': 'No missing product records found to clear.',
                        'type': 'info',
                        'sticky': False,
                    }
                }
        except Exception as e:
            _logger.error(f"Error clearing missing products: {str(e)}")
            raise UserError(f"Failed to clear missing products: {str(e)}")
    
    def sync_products_ultra_safe(self):
        """Ultra-safe sync method for very large inventories (8000+ products) with minimal chunk size"""
        try:
            _logger.info("Starting ultra-safe sync for large inventory...")
            # Use very small chunks and longer delays
            self.sync_products_chunked(chunk_size=10)
        except Exception as e:
            _logger.error(f"Ultra-safe sync failed: {str(e)}")
            raise
    
    def _send_email_report_if_enabled(self):
        """Send email report if enabled in panel configuration"""
        try:
            panel_config = self.env['warehouse.sync.panel'].search([], limit=1)
            if panel_config and panel_config.send_email_report:
                panel_config.send_sync_report_email()
        except Exception as e:
            _logger.error(f"Error sending email report: {str(e)}")
    
    def _clear_previous_logs(self):
        """Clear previous sync logs and unpublished products, but preserve missing products for analysis"""
        try:
            # Clear all sync logs
            sync_logs = self.env['warehouse.sync.log'].search([])
            if sync_logs:
                sync_logs.unlink()
                _logger.info(f"Cleared {len(sync_logs)} previous sync log entries")
            
            # DON'T clear missing products - keep them for analysis
            # Only clear missing products that are older than 1 day to prevent database bloat
            old_missing_cutoff = fields.Datetime.now() - timedelta(days=1)
            old_missing_products = self.env['warehouse.missing.products'].search([
                ('create_date', '<', old_missing_cutoff)
            ])
            if old_missing_products:
                old_missing_products.unlink()
                _logger.info(f"Cleared {len(old_missing_products)} old missing product entries (>1 day)")

            # Clear all unpublished products records
            unpublished_products = self.env['warehouse.unpublished.products'].search([])
            if unpublished_products:
                unpublished_products.unlink()
                _logger.info(f"Cleared {len(unpublished_products)} previous unpublished product entries")

            _logger.info("Previous logs cleared successfully - missing products preserved for analysis")

        except Exception as e:
            _logger.error("Error clearing previous logs: %s", str(e))
            # Don't raise exception here - we want sync to continue even if log clearing fails

    def sync_products(self):
        """
        This method calls the external API, processes ALL inventory products,
        updates Odoo stock and logs all products regardless of stock level.
        """
        url = "Your_API_Endpoint_Here"  # Replace with actual API endpoint
        try:
            _logger.info(f"Calling API endpoint: {url}")
            response = requests.get(url)       # Call the external API
            _logger.info(f"API response status code: {response.status_code}")
            
            try:
                payload = response.json()      # Parse the response JSON
                _logger.info(f"API response data sample: {str(payload)[:500]}...")  # Log first 500 chars of response
            except Exception as e:
                _logger.error(f"Failed to parse API response as JSON: {str(e)}")
                _logger.error(f"Response content: {response.text[:1000]}")
                raise

            if not payload.get("success"):
                _logger.error(f"API reported failure. Response: {payload}")
                return
                
            if "data" not in payload:
                _logger.error("No 'data' key in API response. Full response: %s", payload)
                return

            # Create lookup dictionaries using normalized identifiers
            api_products_by_sku = {}
            api_products_by_barcode = {}
            processed_skus = set()

            for item in payload["data"]:
                raw_sku = item.get("sku")
                raw_barcode = item.get("barcode")
                sku = self._sanitize_identifier(raw_sku)
                barcode = self._sanitize_identifier(raw_barcode)
                brand_raw = item.get("brand")
                brand = self._sanitize_identifier(brand_raw)
                ext_id_raw = item.get("itemNumber")
                ext_id = self._sanitize_identifier(ext_id_raw)
                qty = float(item.get("southbayStock") or 0.0)

                barcode_clean = barcode if barcode and barcode.lower() != 'none' else None
                brand_clean = brand if brand and brand.lower() != 'none' else None
                entry = {
                    'ext_id': ext_id or None,
                    'qty': qty,
                    'sku': sku or None,
                    'barcode': barcode_clean,
                    'brand': brand_clean,
                }

                normalized_sku = self._normalize_identifier(sku)
                entry['normalized_sku'] = normalized_sku
                if normalized_sku:
                    api_products_by_sku[normalized_sku] = entry

                normalized_barcode = self._normalize_identifier(barcode)
                entry['normalized_barcode'] = normalized_barcode
                if normalized_barcode:
                    api_products_by_barcode[normalized_barcode] = entry

            # Get ALL products from Odoo inventory (including both stockable and consumable products)
            all_products = self.env["product.product"].search([('type', 'in', ['product', 'consu'])])
            
            # Debug: Log first few products found
            _logger.info(f"Found {len(all_products)} products in Odoo inventory")
            for i, prod in enumerate(all_products[:5], 1):  # Log first 5 products for debugging
                _logger.info(f"Product {i}: ID={prod.id}, Name='{prod.name}', SKU='{prod.default_code}', Type='{prod.type}'")
            
            _logger.info(f"Found {len(payload["data"])} products in API response")
            _logger.info(f"Processing {len(all_products)} products from inventory...")
            
            # Process each product in Odoo inventory
            for product in all_products:
                # Check for valid SKU and barcode directly
                raw_sku = product.default_code
                raw_barcode = product.barcode
                
                # A valid SKU/barcode must exist, not be empty, and not be placeholder values
                has_sku = bool(raw_sku and str(raw_sku).strip() and str(raw_sku).strip().lower() not in ['none', 'null', 'n/a', 'na', ''])
                has_barcode = bool(raw_barcode and str(raw_barcode).strip() and str(raw_barcode).strip().lower() not in ['none', 'null', 'n/a', 'na', ''])
                
                # Normalize identifiers for API lookup
                sku = self._sanitize_identifier(raw_sku)
                barcode = self._sanitize_identifier(raw_barcode)
                normalized_sku = self._normalize_identifier(sku)
                normalized_barcode = self._normalize_identifier(barcode)
                
                # Debug: Log first 5 products to see what's happening
                if product.id <= 5:
                    _logger.info(f"DEBUG Product {product.id}: default_code='{raw_sku}', barcode='{raw_barcode}', has_sku={has_sku}, has_barcode={has_barcode}")
                identifiers_complete = has_sku and has_barcode
                should_process = has_sku or has_barcode
                is_stockable = self._is_stockable_product(product)

                # Look up this product in API data using normalized identifiers
                api_data = None
                if normalized_sku and normalized_sku in api_products_by_sku:
                    api_data = api_products_by_sku[normalized_sku]
                if not api_data and normalized_barcode and normalized_barcode in api_products_by_barcode:
                    api_data = api_products_by_barcode[normalized_barcode]
                
                if api_data and api_data.get('normalized_sku'):
                    processed_skus.add(api_data['normalized_sku'])

                if api_data:
                    # Product found in API - use API quantity
                    qty = api_data['qty']
                    ext_id = api_data['ext_id']
                    api_brand = api_data.get('brand')
                else:
                    # Product not found in API - treat as 0 quantity (but don't log as missing)
                    qty = 0.0
                    ext_id = None
                    api_brand = None

                # Apply stock rules (mapping warehouse qty to Odoo qty)
                if qty == 0:
                    new_qty = 0
                elif 30 <= qty <= 50:
                    new_qty = 2
                elif qty < 30:
                    new_qty = 0
                elif 50 < qty < 200:
                    new_qty = 5
                elif qty >= 200:
                    new_qty = 10
                else:
                    new_qty = product.qty_available  # fallback to current Odoo stock if unmatched

                odoo_qty = product.qty_available
                alert = new_qty < 5  # Flag for low stock alert

                # Log the sync result for this product
                # Ensure we have valid values for all fields
                log_sku = sku if sku else f"NO_SKU_{product.id}"
                log_barcode = barcode if barcode and barcode.lower() != 'none' else None
                log_ext_id = ext_id if ext_id else None
                
                if not should_process:
                    log_note = "SKIPPED: missing SKU and barcode. No changes applied."
                else:
                    if not api_data:
                        log_note = "No API match; treated as zero inventory."
                    elif alert:
                        log_note = "LOW STOCK"
                    elif new_qty != odoo_qty:
                        log_note = f"Stock rule triggered: new Odoo qty = {new_qty}"
                    else:
                        log_note = "Stock OK"

                    if api_data and not (has_sku and has_barcode):
                        missing_parts = []
                        if not has_sku:
                            missing_parts.append("SKU")
                        if not has_barcode:
                            missing_parts.append("barcode")
                        if missing_parts:
                            joined = " and ".join(missing_parts)
                            log_note += f" (missing {joined} - UNPUBLISHED)"

                self.env["warehouse.sync.log"].create({
                    "sku": log_sku,
                    "brand": api_brand,
                    "main_id": log_ext_id,
                    "barcode": log_barcode,
                    "quantity": new_qty,
                    "alert": alert,
                    "note": log_note
                })

                # Only update Odoo if at least one identifier is available and stock changed
                if should_process and is_stockable and new_qty != odoo_qty:
                    location = self.env.ref("stock.stock_location_stock")
                    
                    # Use proper inventory adjustment for Odoo 18
                    quant = self.env['stock.quant'].sudo().search([
                        ('product_id', '=', product.id),
                        ('location_id', '=', location.id)
                    ], limit=1)
                    
                    if quant:
                        # Update existing quant
                        quant.sudo().write({'inventory_quantity': new_qty})
                        quant.sudo().action_apply_inventory()
                    else:
                        # Create new quant with proper inventory adjustment
                        new_quant = self.env['stock.quant'].sudo().create({
                            'product_id': product.id,
                            'location_id': location.id,
                            'inventory_quantity': new_qty,
                            'quantity': 0
                        })
                        new_quant.sudo().action_apply_inventory()

                # Publish only when both SKU and barcode are present; otherwise keep it unpublished
                template = product.product_tmpl_id
                if template:
                    # Publish only when both SKU and barcode exist; unpublish if either is missing
                    publish = bool(has_sku and has_barcode)
                    old_status = template.website_published
                    template.sudo().write({'website_published': publish})
                    
                    # Log ALL unpublished products (regardless of whether they have API data or not)
                    if not publish:  # If product is unpublished for any reason
                        missing_parts = []
                        unpublish_reason = "Unknown reason"
                        
                        if not has_sku and not has_barcode:
                            missing_parts = ["SKU", "barcode"]
                            unpublish_reason = "Missing both SKU and barcode"
                        elif not has_sku:
                            missing_parts = ["SKU"]
                            unpublish_reason = "Missing SKU"
                        elif not has_barcode:
                            missing_parts = ["barcode"]
                            unpublish_reason = "Missing barcode"
                        
                        # Create unpublished product log entry
                        self.env["warehouse.unpublished.products"].create({
                            "sku": sku if sku else f"NO_SKU_{product.id}",
                            "brand": api_brand if api_data else (product.product_tmpl_id.brand or "Unknown"),
                            "main_id": ext_id if api_data else None,
                            "barcode": barcode if barcode and barcode.lower() != 'none' else None,
                            "quantity": qty if api_data else 0.0,
                            "missing_fields": " and ".join(missing_parts) if missing_parts else "Other",
                            "sync_date": fields.Datetime.now(),
                            "note": f"Product unpublished: {unpublish_reason}. " + 
                                   (f"API quantity: {qty}" if api_data else "No API data found")
                        })
                    
                    # Debug: Log first 5 products to see publishing changes
                    if product.id <= 5:
                        _logger.info(f"DEBUG Product {product.id} publishing: old={old_status}, new={publish}, has_sku={has_sku}, has_barcode={has_barcode}")

            # Now check for API products that were NOT found in Odoo inventory
            # Get configuration from warehouse sync panel
            panel_config = self.env['warehouse.sync.panel'].search([], limit=1)
            filter_enabled = panel_config.filter_missing_products if panel_config else False
            
            # Parse allowed prefixes from configuration
            allowed_prefixes = []
            if filter_enabled and panel_config and panel_config.allowed_prefixes:
                allowed_prefixes = [prefix.strip() for prefix in panel_config.allowed_prefixes.split(',') if prefix.strip()]
            
            for item in payload["data"]:
                raw_sku = item.get("sku")
                raw_barcode = item.get("barcode")
                sku = self._sanitize_identifier(raw_sku)
                brand_raw = item.get("brand")
                brand = self._sanitize_identifier(brand_raw)
                barcode = self._sanitize_identifier(raw_barcode)
                ext_id_raw = item.get("itemNumber")
                ext_id = self._sanitize_identifier(ext_id_raw)
                qty = float(item.get("southbayStock") or 0.0)
                
                # Apply prefix filtering if enabled
                if filter_enabled and allowed_prefixes:
                    if not (sku and any(sku.startswith(prefix) for prefix in allowed_prefixes)):
                        continue  # Skip products that don't match allowed prefixes
                
                normalized_sku = self._normalize_identifier(sku)
                if not normalized_sku:
                    continue

                # Check if this API product was processed (found in inventory)
                if normalized_sku in processed_skus:
                    continue

                # If not processed, it means this API product is missing from inventory
                self.env["warehouse.missing.products"].create({
                    "sku": sku.strip(),
                    "brand": brand if brand and brand.lower() != 'none' else None,
                    "main_id": ext_id if ext_id else None,
                    "barcode": barcode if barcode and barcode.lower() != 'none' else None,
                    "quantity": qty,
                    "sync_date": fields.Datetime.now(),
                    "note": f"Product found in API but missing from Odoo inventory. API quantity: {qty}"
                })

            # Commit all changes to ensure they are persisted
            self.env.cr.commit()
            _logger.info("✅ SyncVision sync completed successfully.")

        except Exception as e:
            _logger.exception("Error syncing warehouse: %s", str(e))
    
    def _send_email_report_if_enabled(self):
        """Send email report if email notifications are enabled"""
        try:
            panel_config = self.env['warehouse.sync.panel'].search([], limit=1)
            if panel_config and panel_config.send_email_report:
                panel_config.send_sync_report_email()
        except Exception as e:
            _logger.error(f"Failed to send email report: {str(e)}")
