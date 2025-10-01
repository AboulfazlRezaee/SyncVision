import re
from odoo import models, fields, api, _  # type: ignore
from odoo.exceptions import UserError  # type: ignore


class WarehouseMissingProducts(models.Model):
    _name = 'warehouse.missing.products'
    _description = 'Products from API not found in Odoo Inventory'
    _order = 'create_date desc'

    # ========== HELPERS (Missing-Products scope only) ==========
    @api.model
    def _sv_norm_sku(self, s: str) -> str:
        """Normalize SKU by removing non-alphanumerics and uppercasing."""
        if not s:
            return ''
        return re.sub(r'[^0-9A-Za-z]+', '', s).upper()

    @api.model
    def _sv_find_product_by_sku_only_relaxed(self, api_sku: str):
        """
        Find a product by SKU ONLY (default_code) but ignoring hyphens/spaces.
        - Compares normalized(default_code) == normalized(api_sku)
        - Searches variants first, then template fallback
        - Does NOT consider barcode (by design for missing-products list)
        - active_test=False so archived products are respected
        """
        norm_target = self._sv_norm_sku(api_sku)
        if not norm_target:
            return self.env['product.product']  # empty

        Product = self.env['product.product'].with_context(active_test=False)
        Template = self.env['product.template'].with_context(active_test=False)

        # Narrow candidates a bit to avoid scanning everything
        head = norm_target[:4]
        tail = norm_target[-4:]
        cand_domain = ['&', ('default_code', 'ilike', head), ('default_code', 'ilike', tail)]

        # Check product variants
        candidates = Product.search(cand_domain, limit=200)
        for p in candidates:
            if self._sv_norm_sku(p.default_code) == norm_target:
                return p

        # Fallback: some deployments store SKU on template
        tmpl_candidates = Template.search(cand_domain, limit=200)
        for t in tmpl_candidates:
            if self._sv_norm_sku(t.default_code) == norm_target:
                if t.product_variant_id:
                    return t.product_variant_id
                if t.product_variant_ids:
                    return t.product_variant_ids[:1]
                return Product  # empty

        # Final direct equality fallback (non-normalized) if present
        direct = Product.search([('default_code', '=', api_sku)], limit=1)
        if direct:
            return direct

        return Product  # empty recordset

    # ========== FIELDS ==========
    sku = fields.Char('SKU', required=True)
    main_id = fields.Char('External ID')
    barcode = fields.Char('Barcode')
    quantity = fields.Float('API Quantity')
    brand = fields.Char('Brand')

    # Sync information
    create_date = fields.Datetime('Date Found Missing', readonly=True)
    sync_date = fields.Datetime('Last Sync Date', readonly=True)

    # Status
    status = fields.Selection([
        ('missing', 'Missing from Inventory'),
        ('created', 'Product Created'),
        ('ignored', 'Ignored')
    ], string='Status', default='missing')

    note = fields.Text('Notes')

    # ========== OVERRIDE CREATE ==========
    @api.model
    def create(self, vals):
        """
        Guardrail: if a product already exists in Odoo by relaxed-SKU match,
        we do NOT treat it as missing. We mark it as 'ignored' with a note
        so it won't show in the default 'Missing' view.
        Supports both single-dict and list-of-dicts create calls.
        """

        def _prepare_vals(v):
            v = dict(v or {})
            api_sku = v.get('sku') or v.get('default_code') or ''
            exists = self._sv_find_product_by_sku_only_relaxed(api_sku)
            if exists:
                # Do not show as missing; keep a quiet audit trail.
                v['status'] = 'ignored'
                msg = f"Ignored: SKU matches existing product (normalized). Product ID {exists.id}."
                v['note'] = (v.get('note') + "\n" + msg) if v.get('note') else msg
            return v

        if isinstance(vals, list):
            prepped = [_prepare_vals(v) for v in vals]
            return super(WarehouseMissingProducts, self).create(prepped)
        else:
            prepped = _prepare_vals(vals)
            return super(WarehouseMissingProducts, self).create(prepped)

    # ========== ACTIONS ==========
    def action_create_product(self):
        """Create the missing product in inventory"""
        self.ensure_one()

        # Base values
        product_vals = {
            'name': f'Product {self.sku}' if self.sku else f'Product {self.main_id}',
            'default_code': self.sku,
            'barcode': self.barcode,
            'categ_id': self.env.ref('product.product_category_all').id,
        }

        # Resolve correct field and value for a stockable product across versions/customizations
        product_model = self.env['product.product']

        # Helper: pick a safe stockable code from a selection
        def _pick_stockable_code(selection):
            preferred_codes = ['product', 'storable', 'goods', 'stockable', 'normal', 'physical', 'item']
            codes = [key for key, _ in (selection or [])]
            for code in preferred_codes:
                if code in codes:
                    return code
            for key, label in (selection or []):
                lbl = (label or '').lower()
                if any(hint in lbl for hint in ['storable', 'stockable', 'inventory', 'goods', 'physical']):
                    return key
            for key in codes:
                if key not in ('service',):
                    return key
            return None

        # Prefer detailed_type when available, otherwise type. Map dynamically.
        field_name = None
        if 'detailed_type' in product_model._fields:
            field_name = 'detailed_type'
        elif 'type' in product_model._fields:
            field_name = 'type'

        if field_name:
            meta = product_model.fields_get([field_name]).get(field_name, {})
            selection = meta.get('selection', [])
            code = _pick_stockable_code(selection)
            if code:
                product_vals[field_name] = code

        # Set brand only if the field exists on product model (added by this module)
        if 'brand' in product_model._fields and self.brand:
            product_vals['brand'] = self.brand

        product = product_model.create(product_vals)

        # Update status
        self.status = 'created'
        self.note = f'Product created: {product.name} (ID: {product.id})'

        # Open the created product
        return {
            'name': f'Created Product: {product.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'product.product',
            'res_id': product.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_ignore_product(self):
        """Mark this missing product as ignored"""
        self.ensure_one()
        self.status = 'ignored'
        self.note = 'Marked as ignored by user'
