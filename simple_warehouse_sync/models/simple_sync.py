import json
import logging
import re
import time
import requests

from odoo import api, fields, models
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

# Defaults used when no configuration is provided (generic/placeholder values)
DEFAULT_API_URL = "https://example.api.com"
DEFAULT_STOCK_MAPPING = [
    {"min": 0, "max": 0, "qty": 0},
    {"min": 0, "max": 30, "qty": 0},
    {"min": 30, "max": 50, "qty": 2},
    {"min": 50, "max": 200, "qty": 5},
    {"min": 200, "qty": 10},
]
DEFAULT_PRIORITY_BRANDS = ["Brand1", "Brand2", "Brand3", "Brand4", "Brand5"]
SETTINGS_BLOB_KEY = "simple_warehouse_sync.settings_blob"


class SimpleWarehouseSync(models.Model):
    """Entry point for simple stock synchronization."""

    _name = "simple.warehouse.sync"
    _description = "Simple Warehouse Stock Sync"

    # Dashboard statistics (computed from latest sync log + lines)
    last_sync_date = fields.Datetime(string="Last Sync", compute="_compute_dashboard_stats", store=False)
    total_synced = fields.Integer(string="Total Synced", compute="_compute_dashboard_stats", store=False)
    published_count = fields.Integer(string="Published Count", compute="_compute_dashboard_stats", store=False)
    low_stock_count = fields.Integer(string="Low Stock Count", compute="_compute_dashboard_stats", store=False)
    high_stock_count = fields.Integer(string="High Stock Count", compute="_compute_dashboard_stats", store=False)
    unpublished_count = fields.Integer(string="Unpublished Count", compute="_compute_dashboard_stats", store=False)
    missing_count = fields.Integer(string="Missing Count", compute="_compute_dashboard_stats", store=False)
    priority_missing_count = fields.Integer(string="Priority Missing Count", compute="_compute_dashboard_stats", store=False)
    latest_log_status = fields.Selection([("success", "Success"), ("fail", "Fail")], string="Latest Status", compute="_compute_health_info", store=False)
    latest_log_date = fields.Datetime(string="Latest Log Date", compute="_compute_health_info", store=False)
    latest_log_message = fields.Text(string="Latest Log Message", compute="_compute_health_info", store=False)
    next_cron_at = fields.Datetime(string="Next Cron Run", compute="_compute_health_info", store=False)
    latest_total_api = fields.Integer(string="Latest API Products", compute="_compute_health_info", store=False)
    latest_matched = fields.Integer(string="Latest Matched", compute="_compute_health_info", store=False)
    latest_updated = fields.Integer(string="Latest Updated", compute="_compute_health_info", store=False)
    latest_missing = fields.Integer(string="Latest Missing", compute="_compute_health_info", store=False)
    latest_api_status_code = fields.Integer(string="Latest API Status", compute="_compute_health_info", store=False)
    latest_api_latency_ms = fields.Integer(string="Latest API Latency (ms)", compute="_compute_health_info", store=False)

    # User-configurable settings (stored in ir.config_parameter, edited on the dashboard)
    # These fields use compute + inverse to properly capture user changes
    sync_api_url = fields.Char(string="Sync API URL", compute="_compute_settings", inverse="_inverse_sync_api_url", readonly=False, store=False)
    sync_api_token = fields.Char(string="API Token", compute="_compute_settings", inverse="_inverse_sync_api_token", readonly=False, store=False)
    sync_api_timeout = fields.Integer(string="API Timeout (s)", compute="_compute_settings", inverse="_inverse_sync_api_timeout", readonly=False, store=False)
    sync_stock_mapping = fields.Text(string="Stock Mapping JSON", compute="_compute_settings", inverse="_inverse_sync_stock_mapping", readonly=False, store=False)
    sync_stock_mapping_text = fields.Text(
        string="Stock Mapping (min,max,qty per line)",
        compute="_compute_settings",
        inverse="_inverse_sync_stock_mapping_text",
        readonly=False,
        store=False,
        help="Enter one rule per line as: min,max,qty (use blank for max to mean no upper bound).",
    )
    sync_priority_brands = fields.Char(string="Priority Brands", compute="_compute_settings", inverse="_inverse_sync_priority_brands", readonly=False, store=False)
    sync_missing_sku_prefixes = fields.Char(string="Ignore Missing SKU Prefixes", compute="_compute_settings", inverse="_inverse_sync_missing_sku_prefixes", readonly=False, store=False)
    sync_low_stock_threshold = fields.Integer(string="Low Stock Threshold", compute="_compute_settings", inverse="_inverse_sync_low_stock_threshold", readonly=False, store=False)
    sync_email_to = fields.Char(string="Notification Recipients", compute="_compute_settings", inverse="_inverse_sync_email_to", readonly=False, store=False)
    sync_cron_interval_number = fields.Integer(string="Cron Interval Number", compute="_compute_settings", inverse="_inverse_sync_cron_interval_number", readonly=False, store=False)
    sync_cron_interval_type = fields.Selection(
        [
            ("minutes", "Minutes"),
            ("hours", "Hours"),
            ("days", "Days"),
            ("weeks", "Weeks"),
            ("months", "Months"),
        ],
        string="Cron Interval Type",
        compute="_compute_settings",
        inverse="_inverse_sync_cron_interval_type",
        readonly=False,
        store=False,
    )
    sync_cron_active = fields.Boolean(string="Enable Auto-sync", compute="_compute_settings", inverse="_inverse_sync_cron_active", readonly=False, store=False)

    # Temporary storage for pending changes (not persisted until Save button is clicked)
    _pending_settings = {}

    def _normalize_sku(self, sku):
        """Normalize SKU by removing non-alphanumerics and uppercasing."""
        if not sku:
            return ""
        return re.sub(r"[^0-9A-Za-z]+", "", str(sku)).upper()

    def _has_identifier(self, value):
        """Return True if SKU/barcode value is meaningful (not empty/'none')."""
        if not value:
            return False
        s = str(value).strip()
        if not s:
            return False
        if s.lower() in ("none", "null", "n/a", "na"):
            return False
        return True

    # ==== Configuration helpers (read from system parameters) ====

    def _split_list_param(self, raw_value):
        """Return a clean list from a comma-separated string parameter."""
        if not raw_value:
            return []
        return [item.strip() for item in str(raw_value).split(",") if item.strip()]

    def _mapping_json_to_text(self, mapping_str):
        """Convert stored JSON mapping to user-editable line format."""
        try:
            mapping = json.loads(mapping_str) if mapping_str else DEFAULT_STOCK_MAPPING
            if not isinstance(mapping, list):
                mapping = DEFAULT_STOCK_MAPPING
        except Exception:
            mapping = DEFAULT_STOCK_MAPPING
        lines = []
        for rule in mapping:
            try:
                min_q = "" if rule.get("min") is None else rule.get("min")
                max_q = "" if rule.get("max") is None else rule.get("max")
                qty = "" if rule.get("qty") is None else rule.get("qty")
                lines.append(f"{min_q},{max_q},{qty}")
            except Exception:
                continue
        return "\n".join(lines)

    def _mapping_text_to_json(self, text_value):
        """Convert user-entered lines (min,max,qty) to JSON list."""
        if not text_value:
            return DEFAULT_STOCK_MAPPING
        rules = []
        for raw_line in str(text_value).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                min_q = float(parts[0]) if parts[0] != "" else 0.0
                max_q = float(parts[1]) if parts[1] != "" else None
                qty = float(parts[2]) if parts[2] != "" else 0.0
                rules.append({"min": min_q, "max": max_q, "qty": qty})
            except Exception:
                continue
        return rules or DEFAULT_STOCK_MAPPING

    def _get_settings_blob(self):
        """Load and parse settings blob from system parameters."""
        icp = self.env["ir.config_parameter"].sudo()
        blob_raw = icp.get_param(SETTINGS_BLOB_KEY)
        if not blob_raw:
            return {}
        try:
            return json.loads(blob_raw)
        except Exception as e:
            _logger.warning("SimpleWarehouseSync: Failed to parse settings blob: %s", e)
            return {}

    def _compute_settings(self):
        """Load settings from system parameters into computed fields."""
        for rec in self:
            blob = rec._get_settings_blob()
            icp = self.env["ir.config_parameter"].sudo()

            # Load each setting with proper fallbacks
            rec.sync_api_url = blob.get("api_url") or icp.get_param("simple_warehouse_sync.api_url") or DEFAULT_API_URL
            rec.sync_api_token = blob.get("api_token") or icp.get_param("simple_warehouse_sync.api_token") or ""
            
            try:
                rec.sync_api_timeout = int(blob.get("api_timeout") or icp.get_param("simple_warehouse_sync.api_timeout") or 60)
            except (ValueError, TypeError):
                rec.sync_api_timeout = 60

            # Stock mapping: load JSON and convert to text
            mapping_json = blob.get("stock_mapping") or icp.get_param("simple_warehouse_sync.stock_mapping") or json.dumps(DEFAULT_STOCK_MAPPING)
            rec.sync_stock_mapping = mapping_json
            rec.sync_stock_mapping_text = rec._mapping_json_to_text(mapping_json)

            rec.sync_priority_brands = blob.get("priority_brands") or icp.get_param("simple_warehouse_sync.priority_brands") or ",".join(DEFAULT_PRIORITY_BRANDS)
            rec.sync_missing_sku_prefixes = blob.get("missing_sku_prefixes") or icp.get_param("simple_warehouse_sync.missing_sku_prefixes") or ""

            try:
                rec.sync_low_stock_threshold = int(blob.get("low_stock_threshold") or icp.get_param("simple_warehouse_sync.low_stock_threshold") or 5)
            except (ValueError, TypeError):
                rec.sync_low_stock_threshold = 5

            rec.sync_email_to = blob.get("email_to") or icp.get_param("simple_warehouse_sync.email_to") or ""

            try:
                rec.sync_cron_interval_number = int(blob.get("cron_interval_number") or icp.get_param("simple_warehouse_sync.cron_interval_number") or 1)
            except (ValueError, TypeError):
                rec.sync_cron_interval_number = 1
            
            rec.sync_cron_interval_type = blob.get("cron_interval_type") or icp.get_param("simple_warehouse_sync.cron_interval_type") or "days"
            
            cron_active_str = blob.get("cron_active") if "cron_active" in blob else icp.get_param("simple_warehouse_sync.cron_active", "True")
            rec.sync_cron_active = str(cron_active_str).lower() in ("true", "1", "yes")

    # Inverse methods - store pending changes in record cache until Save button is clicked
    def _get_pending_key(self, field_name):
        """Generate a unique key for pending settings storage."""
        return f"{self.id or 'new'}_{field_name}"

    def _set_pending(self, field_name, value):
        """Store a pending value for later save."""
        key = self._get_pending_key(field_name)
        SimpleWarehouseSync._pending_settings[key] = value

    def _get_pending(self, field_name, default=None):
        """Get a pending value if it exists."""
        key = self._get_pending_key(field_name)
        return SimpleWarehouseSync._pending_settings.get(key, default)

    def _has_pending(self, field_name):
        """Check if a pending value exists."""
        key = self._get_pending_key(field_name)
        return key in SimpleWarehouseSync._pending_settings

    def _clear_pending(self):
        """Clear all pending settings for this record."""
        prefix = f"{self.id or 'new'}_"
        keys_to_remove = [k for k in SimpleWarehouseSync._pending_settings if k.startswith(prefix)]
        for k in keys_to_remove:
            del SimpleWarehouseSync._pending_settings[k]

    def _inverse_sync_api_url(self):
        for rec in self:
            rec._set_pending('sync_api_url', rec.sync_api_url)

    def _inverse_sync_api_token(self):
        for rec in self:
            rec._set_pending('sync_api_token', rec.sync_api_token)

    def _inverse_sync_api_timeout(self):
        for rec in self:
            rec._set_pending('sync_api_timeout', rec.sync_api_timeout)

    def _inverse_sync_stock_mapping(self):
        for rec in self:
            rec._set_pending('sync_stock_mapping', rec.sync_stock_mapping)

    def _inverse_sync_stock_mapping_text(self):
        for rec in self:
            rec._set_pending('sync_stock_mapping_text', rec.sync_stock_mapping_text)

    def _inverse_sync_priority_brands(self):
        for rec in self:
            rec._set_pending('sync_priority_brands', rec.sync_priority_brands)

    def _inverse_sync_missing_sku_prefixes(self):
        for rec in self:
            rec._set_pending('sync_missing_sku_prefixes', rec.sync_missing_sku_prefixes)

    def _inverse_sync_low_stock_threshold(self):
        for rec in self:
            rec._set_pending('sync_low_stock_threshold', rec.sync_low_stock_threshold)

    def _inverse_sync_email_to(self):
        for rec in self:
            rec._set_pending('sync_email_to', rec.sync_email_to)

    def _inverse_sync_cron_interval_number(self):
        for rec in self:
            rec._set_pending('sync_cron_interval_number', rec.sync_cron_interval_number)

    def _inverse_sync_cron_interval_type(self):
        for rec in self:
            rec._set_pending('sync_cron_interval_type', rec.sync_cron_interval_type)

    def _inverse_sync_cron_active(self):
        for rec in self:
            rec._set_pending('sync_cron_active', rec.sync_cron_active)

    def _persist_settings_from_form(self):
        """Save all form values to system parameters at once (only called when Save button is clicked)."""
        self.ensure_one()
        
        icp = self.env["ir.config_parameter"].sudo()

        # Try to read raw payload data (helps when computed, non-stored fields are not propagated on button click)
        params = self.env.context.get("params") or {}
        payload_candidates = []
        # Odoo may place the form data in different spots depending on the client call stack.
        payload_candidates.append(params.get("data"))
        payload_candidates.append(params.get("context", {}).get("data"))
        payload_candidates.append(self.env.context.get("data"))
        payload_candidates.append(self.env.context.get("record_data"))
        for arg in params.get("args") or []:
            if isinstance(arg, dict):
                payload_candidates.append(arg)
        ctx = params.get("context") or {}
        payload_candidates.append(ctx.get("params", {}).get("data") if isinstance(ctx, dict) else None)
        payload_data = next((c for c in payload_candidates if c), {}) or {}
        # Last resort: try the HTTP JSON payload if available
        try:
            from odoo.http import request

            if not payload_data and getattr(request, "jsonrequest", None):
                payload_data = (
                    request.jsonrequest.get("params", {}).get("data")
                    or request.jsonrequest.get("params", {}).get("args", [{}])[0]
                    or {}
                )
        except Exception:
            payload_data = payload_data or {}

        def _get_payload_value(field_name, fallback=None):
            """Prefer pending value (from inverse), then payload, then record value, then fallback."""
            # First check pending settings (captured by inverse methods when user edits fields)
            if self._has_pending(field_name):
                return self._get_pending(field_name)
            # Then check posted payload data
            if field_name in payload_data:
                return payload_data.get(field_name)
            # Then try record attribute (will re-compute from saved values)
            if hasattr(self, field_name):
                return getattr(self, field_name)
            return fallback

        def _to_bool(val, default=False):
            if val is None:
                return default
            if isinstance(val, bool):
                return val
            return str(val).lower() in ("true", "1", "yes", "y", "on")
        
        # Load current blob so we can preserve values that are not present in the form payload
        current_blob = self._get_settings_blob()

        # If no blob exists yet, initialize with defaults
        if not current_blob:
            current_blob = {
                "api_url": DEFAULT_API_URL,
                "api_token": "",
                "api_timeout": 60,
                "stock_mapping": json.dumps(DEFAULT_STOCK_MAPPING),
                "priority_brands": ",".join(DEFAULT_PRIORITY_BRANDS),
                "missing_sku_prefixes": "",
                "low_stock_threshold": 5,
                "email_to": "",
                "cron_interval_number": 1,
                "cron_interval_type": "days",
                "cron_active": "True",
            }
        
        # Build new blob from form values - take ALL values from the form with sensible fallbacks
        new_blob = {}
        
        # API URL
        api_url_val = _get_payload_value("sync_api_url", None)
        new_blob["api_url"] = api_url_val.strip() if api_url_val else current_blob.get("api_url", DEFAULT_API_URL)
        
        # API Token - empty is valid
        token_val = _get_payload_value("sync_api_token", None)
        new_blob["api_token"] = token_val if token_val is not None else current_blob.get("api_token", "")
        
        # API Timeout
        try:
            timeout_val = _get_payload_value("sync_api_timeout", None)
            timeout = int(timeout_val) if timeout_val not in (None, False) else int(current_blob.get("api_timeout", 60))
            new_blob["api_timeout"] = max(timeout, 1)
        except (ValueError, TypeError):
            new_blob["api_timeout"] = int(current_blob.get("api_timeout", 60))
        
        # Stock mapping
        stock_mapping_text = _get_payload_value("sync_stock_mapping_text", None)
        if stock_mapping_text and str(stock_mapping_text).strip():
            mapping_rules = self._mapping_text_to_json(stock_mapping_text)
            new_blob["stock_mapping"] = json.dumps(mapping_rules)
        else:
            new_blob["stock_mapping"] = current_blob.get("stock_mapping", json.dumps(DEFAULT_STOCK_MAPPING))
        
        # Priority brands - empty string is valid
        priority_val = _get_payload_value("sync_priority_brands", None)
        new_blob["priority_brands"] = priority_val if priority_val is not None else current_blob.get("priority_brands", ",".join(DEFAULT_PRIORITY_BRANDS))
        
        # Missing SKU prefixes - empty string is valid
        missing_pref_val = _get_payload_value("sync_missing_sku_prefixes", None)
        new_blob["missing_sku_prefixes"] = missing_pref_val if missing_pref_val is not None else current_blob.get("missing_sku_prefixes", "")
        
        # Low stock threshold - 0 is valid
        try:
            low_stock_val = _get_payload_value("sync_low_stock_threshold", None)
            threshold = int(low_stock_val) if low_stock_val is not None else int(current_blob.get("low_stock_threshold", 5))
            new_blob["low_stock_threshold"] = max(threshold, 0)
        except (ValueError, TypeError):
            new_blob["low_stock_threshold"] = int(current_blob.get("low_stock_threshold", 5))
        
        # Email recipients - empty string is valid
        email_to_val = _get_payload_value("sync_email_to", None)
        new_blob["email_to"] = email_to_val if email_to_val is not None else current_blob.get("email_to", "")
        
        # Cron interval number
        try:
            interval_val = _get_payload_value("sync_cron_interval_number", None)
            interval = int(interval_val) if interval_val not in (None, False) else int(current_blob.get("cron_interval_number", 1))
            new_blob["cron_interval_number"] = max(interval, 1)
        except (ValueError, TypeError):
            new_blob["cron_interval_number"] = int(current_blob.get("cron_interval_number", 1))
        
        # Cron interval type
        interval_type_val = _get_payload_value("sync_cron_interval_type", None)
        new_blob["cron_interval_type"] = interval_type_val if interval_type_val else current_blob.get("cron_interval_type", "days")
        
        # Cron active - boolean
        cron_active_val = _get_payload_value("sync_cron_active", None)
        cron_active_bool = _to_bool(cron_active_val, default=_to_bool(current_blob.get("cron_active", "True")))
        new_blob["cron_active"] = "True" if cron_active_bool else "False"
        
        # Save the complete blob
        icp.set_param(SETTINGS_BLOB_KEY, json.dumps(new_blob))
        
        # Also save individual params for backward compatibility
        icp.set_param("simple_warehouse_sync.api_url", new_blob["api_url"])
        icp.set_param("simple_warehouse_sync.api_token", new_blob["api_token"])
        icp.set_param("simple_warehouse_sync.api_timeout", str(new_blob["api_timeout"]))
        icp.set_param("simple_warehouse_sync.stock_mapping", new_blob["stock_mapping"])
        icp.set_param("simple_warehouse_sync.priority_brands", new_blob["priority_brands"])
        icp.set_param("simple_warehouse_sync.missing_sku_prefixes", new_blob["missing_sku_prefixes"])
        icp.set_param("simple_warehouse_sync.low_stock_threshold", str(new_blob["low_stock_threshold"]))
        icp.set_param("simple_warehouse_sync.email_to", new_blob["email_to"])
        icp.set_param("simple_warehouse_sync.cron_interval_number", str(new_blob["cron_interval_number"]))
        icp.set_param("simple_warehouse_sync.cron_interval_type", new_blob["cron_interval_type"])
        icp.set_param("simple_warehouse_sync.cron_active", new_blob["cron_active"])
        
        _logger.info("SyncVision: Settings saved successfully")
        
        # Clear pending settings after successful save
        self._clear_pending()
        
        # Update cron schedule
        self._update_cron_schedule()
        
        # Refresh the form to show saved values
        self.invalidate_recordset()

    def action_syncvision_save_settings(self):
        """Persist dashboard settings to system parameters and show a toast."""
        self.ensure_one()
        self._persist_settings_from_form()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "SyncVision",
                "message": "Settings saved successfully.",
                "type": "success",
                "sticky": False,
            },
        }

    def action_syncvision_apply_default_mapping(self):
        """Reset stock mapping to the default recommended rules."""
        self.ensure_one()
        default_text = self._mapping_json_to_text(json.dumps(DEFAULT_STOCK_MAPPING))
        self.sync_stock_mapping_text = default_text
        self._persist_settings_from_form()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "SyncVision",
                "message": "Stock mapping reset to defaults.",
                "type": "success",
                "sticky": False,
            },
        }

    def _get_priority_brands(self):
        icp = self.env["ir.config_parameter"].sudo()
        blob = self._get_settings_blob()
        brands_str = blob.get("priority_brands") or icp.get_param("simple_warehouse_sync.priority_brands") or ",".join(DEFAULT_PRIORITY_BRANDS)
        brands = self._split_list_param(brands_str)
        return [b.upper() for b in brands] if brands else [b.upper() for b in DEFAULT_PRIORITY_BRANDS]

    def _is_priority_brand(self, brand):
        if not brand:
            return False
        return brand.upper() in self._get_priority_brands()

    def _get_missing_prefixes(self):
        icp = self.env["ir.config_parameter"].sudo()
        blob = self._get_settings_blob()
        prefixes_str = blob.get("missing_sku_prefixes") or icp.get_param("simple_warehouse_sync.missing_sku_prefixes") or ""
        prefixes = self._split_list_param(prefixes_str)
        normalized = []
        for prefix in prefixes:
            norm = self._normalize_sku(prefix)
            if norm:
                normalized.append(norm)
        return normalized

    def _should_ignore_missing(self, sku, prefixes):
        """Return True if the SKU should be ignored for missing logs based on configured prefixes."""
        if not sku or not prefixes:
            return False
        norm = self._normalize_sku(sku)
        return any(norm.startswith(pref) for pref in prefixes if pref)

    def _get_low_stock_threshold(self):
        icp = self.env["ir.config_parameter"].sudo()
        blob = self._get_settings_blob()
        try:
            threshold = int(blob.get("low_stock_threshold") or icp.get_param("simple_warehouse_sync.low_stock_threshold") or 5)
        except (ValueError, TypeError):
            threshold = 5
        return max(threshold, 0)

    def _get_brand_name(self, product, api_brand=None):
        """Best-effort brand name from product or API payload."""
        brand = False
        tmpl = getattr(product, "product_tmpl_id", False)
        if tmpl:
            if hasattr(tmpl, "brand_id") and getattr(tmpl, "brand_id"):
                brand = tmpl.brand_id.name
            elif hasattr(tmpl, "brand") and getattr(tmpl, "brand"):
                brand = tmpl.brand
        if not brand:
            if hasattr(product, "brand_id") and getattr(product, "brand_id"):
                brand = product.brand_id.name
            elif hasattr(product, "brand") and getattr(product, "brand"):
                brand = product.brand
        if not brand and api_brand:
            brand = api_brand
        return brand or False

    def _get_stock_mapping(self):
        icp = self.env["ir.config_parameter"].sudo()
        blob = self._get_settings_blob()
        raw = blob.get("stock_mapping") or icp.get_param("simple_warehouse_sync.stock_mapping")
        if raw:
            try:
                mapping = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(mapping, list):
                    return mapping
            except Exception as e:
                _logger.warning("SimpleWarehouseSync: invalid stock_mapping param: %s; falling back to defaults", e)
        return DEFAULT_STOCK_MAPPING

    def _map_api_qty(self, api_qty):
        """Map API quantity to an Odoo-facing quantity using configurable rules."""
        mapping = self._get_stock_mapping()
        for rule in mapping:
            try:
                min_q = float(rule.get("min", float("-inf")))
                max_raw = rule.get("max")
                max_q = float(max_raw) if max_raw is not None else None
                qty_value = float(rule.get("qty", api_qty))
            except Exception:
                continue

            if max_q is None:
                if api_qty >= min_q:
                    return qty_value
            else:
                if min_q <= api_qty < max_q:
                    return qty_value

        # Fallback: mirror API quantity if nothing matched
        return api_qty

    def _get_api_config(self):
        icp = self.env["ir.config_parameter"].sudo()
        blob = self._get_settings_blob()
        api_url = blob.get("api_url") or icp.get_param("simple_warehouse_sync.api_url") or DEFAULT_API_URL
        try:
            timeout = int(blob.get("api_timeout") or icp.get_param("simple_warehouse_sync.api_timeout") or 60)
        except (ValueError, TypeError):
            timeout = 60
        api_token = blob.get("api_token") or icp.get_param("simple_warehouse_sync.api_token") or ""

        headers = {}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"

        return {
            "url": api_url,
            "timeout": max(timeout, 1),
            "headers": headers,
        }
        
    def _update_cron_schedule(self):
        """Update the cron job schedule based on saved settings."""
        cron = self.env.ref("simple_warehouse_sync.ir_cron_simple_warehouse_sync", raise_if_not_found=False)
        if not cron:
            _logger.warning("SimpleWarehouseSync: Cron job not found")
            return
        
        blob = self._get_settings_blob()
        icp = self.env["ir.config_parameter"].sudo()
        
        try:
            interval_number = int(blob.get("cron_interval_number") or icp.get_param("simple_warehouse_sync.cron_interval_number") or 1)
        except (ValueError, TypeError):
            interval_number = 1
        
        interval_type = blob.get("cron_interval_type") or icp.get_param("simple_warehouse_sync.cron_interval_type") or "days"
        active_str = blob.get("cron_active") if "cron_active" in blob else icp.get_param("simple_warehouse_sync.cron_active", "True")
        active = str(active_str).lower() in ("true", "1", "yes")

        try:
            cron_vals = {
                "interval_number": interval_number,
                "interval_type": interval_type,
                "active": active,
            }
            cron.sudo().write(cron_vals)
            
            # If cron is active and nextcall is in the past, reset it
            if cron.active and cron.nextcall and cron.nextcall < fields.Datetime.now():
                cron.sudo().write({"nextcall": fields.Datetime.now()})
        except Exception as e:
            _logger.error("SimpleWarehouseSync: Failed to update cron schedule: %s", e)

    def _compute_dashboard_stats(self):
        Line = self.env["simple.warehouse.sync.line"].sudo()
        Log = self.env["simple.warehouse.sync.log"].sudo()
        latest_log = Log.search([], order="create_date desc", limit=1)

        for rec in self:
            if not latest_log:
                rec.last_sync_date = False
                rec.total_synced = 0
                rec.published_count = 0
                rec.low_stock_count = 0
                rec.high_stock_count = 0
                rec.unpublished_count = 0
                rec.missing_count = 0
                rec.priority_missing_count = 0
                continue

            rec.last_sync_date = latest_log.create_date

            domain_base = [("log_id", "=", latest_log.id)]
            total_synced = Line.search_count(domain_base + [("missing", "=", False)])
            unpublished = Line.search_count(domain_base + [("unpublished", "=", True)])

            rec.total_synced = total_synced
            rec.unpublished_count = unpublished
            rec.published_count = max(total_synced - unpublished, 0)
            rec.low_stock_count = Line.search_count(domain_base + [("low_stock", "=", True), ("missing", "=", False)])
            rec.high_stock_count = Line.search_count(domain_base + [("high_stock", "=", True), ("missing", "=", False)])
            rec.missing_count = Line.search_count(domain_base + [("missing", "=", True)])
            rec.priority_missing_count = Line.search_count(
                domain_base + [("missing", "=", True), ("priority_brand", "=", True)]
            )

    def _compute_health_info(self):
        Log = self.env["simple.warehouse.sync.log"].sudo()
        cron = self.env.ref("simple_warehouse_sync.ir_cron_simple_warehouse_sync", raise_if_not_found=False)
        latest_log = Log.search([], order="create_date desc", limit=1)

        for rec in self:
            rec.latest_log_status = latest_log.status if latest_log else False
            rec.latest_log_date = latest_log.create_date if latest_log else False
            rec.latest_log_message = latest_log.message if latest_log else False
            rec.next_cron_at = cron.nextcall if cron else False
            rec.latest_total_api = latest_log.total_api_products if latest_log else 0
            rec.latest_matched = latest_log.matched_products if latest_log else 0
            rec.latest_updated = latest_log.updated_products if latest_log else 0
            rec.latest_missing = latest_log.missing_products if latest_log else 0
            rec.latest_api_status_code = latest_log.api_status_code if latest_log else False
            rec.latest_api_latency_ms = latest_log.api_latency_ms if latest_log else False

    @api.model
    def run_sync(self):
        """Run a full stock sync from the external API."""
        api_cfg = self._get_api_config()
        Product = self.env["product.product"].sudo()
        Quant = self.env["stock.quant"].sudo()
        Line = self.env["simple.warehouse.sync.line"].sudo()
        missing_prefixes = self._get_missing_prefixes()
        low_stock_threshold = self._get_low_stock_threshold()

        # Determine main internal location
        try:
            main_location = self.env.ref("stock.stock_location_stock")
        except Exception:
            main_location = self.env["stock.location"].sudo().search([
                ("usage", "=", "internal")
            ], limit=1)
        if not main_location:
            _logger.error("SimpleWarehouseSync: No internal stock location found; aborting sync")
            return False

        total_api = 0
        matched = 0
        updated = 0
        missing = 0
        errors = []
        latency_ms = 0
        status_code = 0

        try:
            start_ts = time.time()
            resp = requests.get(api_cfg["url"], timeout=api_cfg["timeout"], headers=api_cfg["headers"])
            latency_ms = int((time.time() - start_ts) * 1000)
            status_code = resp.status_code
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            msg = f"API request failed: {e}"
            _logger.error("SimpleWarehouseSync: %s", msg)
            self.env["simple.warehouse.sync.log"].create({
                "status": "fail",
                "message": msg,
                "api_status_code": status_code or False,
                "api_latency_ms": latency_ms or False,
            })
            return False

        if not payload or not payload.get("success") or "data" not in payload:
            msg = f"Invalid API response structure"
            _logger.error("SimpleWarehouseSync: %s", msg)
            self.env["simple.warehouse.sync.log"].create({
                "status": "fail",
                "message": msg,
                "api_status_code": status_code,
                "api_latency_ms": latency_ms,
            })
            return False

        # Clear previous per-product lines so we only keep the last run
        Line.search([]).unlink()

        # Prepare data and summary log
        data = payload.get("data") or []
        total_api = len(data)

        log = self.env["simple.warehouse.sync.log"].create({
            "status": "success",
            "total_api_products": 0,
            "matched_products": 0,
            "updated_products": 0,
            "missing_products": 0,
            "api_status_code": status_code,
            "api_latency_ms": latency_ms,
        })

        # Build an index of products by normalized SKU for fast matching
        all_products = Product.search([])
        products_by_norm_sku = {}
        for prod in all_products:
            norm = self._normalize_sku(prod.default_code)
            if norm:
                products_by_norm_sku[norm] = prod

        for item in data:
            try:
                sku = (item.get("sku") or "").strip()
                if not sku:
                    continue

                api_qty = float(item.get("southbayStock") or 0.0)

                # Normalized-SKU lookup
                norm_sku = self._normalize_sku(sku)
                product = products_by_norm_sku.get(norm_sku)
                
                if not product:
                    if self._should_ignore_missing(sku, missing_prefixes):
                        continue

                    # Missing product: log as API-only
                    missing += 1
                    brand_name = (item.get("brand") or "") or False
                    priority_flag = self._is_priority_brand(brand_name)
                    Line.create({
                        "log_id": log.id,
                        "product_id": False,
                        "sku": sku,
                        "brand": brand_name,
                        "priority_brand": priority_flag,
                        "external_id": item.get("itemNumber") or False,
                        "barcode": item.get("barcode") or False,
                        "api_qty": api_qty,
                        "qty": 0.0,
                        "low_stock": False,
                        "high_stock": False,
                        "unpublished": False,
                        "missing": True,
                        "note": "Missing in inventory (API only)",
                    })
                    continue

                matched += 1

                # Apply configurable stock mapping rules
                try:
                    new_qty = max(float(self._map_api_qty(api_qty)), 0.0)
                except Exception:
                    new_qty = product.qty_available

                # Use qty_available as baseline and adjust via Quant API
                current_qty = product.qty_available
                diff = new_qty - current_qty

                if diff:
                    Quant._update_available_quantity(product, main_location, diff)
                    updated += 1

                # Publish/unpublish based on having BOTH SKU and barcode
                tmpl = product.product_tmpl_id
                has_sku = self._has_identifier(product.default_code)
                has_barcode = self._has_identifier(product.barcode)
                if tmpl:
                    tmpl.website_published = bool(has_sku and has_barcode)

                # Build per-product status flags and note
                low_stock = new_qty < low_stock_threshold
                high_stock = new_qty >= low_stock_threshold
                if new_qty == 0:
                    note = "OUT OF STOCK"
                elif low_stock:
                    note = "LOW STOCK"
                elif diff:
                    note = f"Stock updated: {current_qty} -> {new_qty}"
                else:
                    note = "Stock OK"

                brand_name = self._get_brand_name(product, item.get("brand"))
                priority_flag = self._is_priority_brand(brand_name)

                Line.create({
                    "log_id": log.id,
                    "product_id": product.id,
                    "sku": product.default_code or sku,
                    "brand": brand_name,
                    "priority_brand": priority_flag,
                    "external_id": item.get("itemNumber") or False,
                    "barcode": product.barcode,
                    "api_qty": api_qty,
                    "qty": new_qty,
                    "low_stock": low_stock,
                    "high_stock": high_stock,
                    "unpublished": not (has_sku and has_barcode),
                    "missing": False,
                    "note": note,
                })

            except Exception as e:
                _logger.error("SimpleWarehouseSync: Error on item %s: %s", item, e)
                errors.append(str(e))
                continue

        # Add log lines for all unpublished products not touched in this run
        try:
            if hasattr(self.env["product.template"], "website_published"):
                existing_line_products = Line.search([
                    ("log_id", "=", log.id),
                    ("product_id", "!=", False),
                ]).mapped("product_id")

                unpublished_products = Product.search([
                    ("product_tmpl_id.website_published", "=", False),
                    ("id", "not in", existing_line_products.ids or [0]),
                ])

                for prod in unpublished_products:
                    has_sku = self._has_identifier(prod.default_code)
                    has_barcode = self._has_identifier(prod.barcode)

                    if not has_sku and not has_barcode:
                        note = "Unpublished: missing SKU and barcode"
                    elif not has_sku:
                        note = "Unpublished: missing SKU"
                    elif not has_barcode:
                        note = "Unpublished: missing barcode"
                    else:
                        note = "Unpublished: website_published is False"

                    qty = prod.qty_available
                    low_stock = qty < low_stock_threshold
                    high_stock = qty >= low_stock_threshold

                    brand_name = self._get_brand_name(prod)
                    priority_flag = self._is_priority_brand(brand_name)

                    Line.create({
                        "log_id": log.id,
                        "product_id": prod.id,
                        "sku": prod.default_code,
                        "brand": brand_name,
                        "priority_brand": priority_flag,
                        "external_id": False,
                        "barcode": prod.barcode,
                        "api_qty": 0.0,
                        "qty": qty,
                        "low_stock": low_stock,
                        "high_stock": high_stock,
                        "unpublished": True,
                        "missing": False,
                        "note": note,
                    })
        except Exception as e:
            _logger.error("SimpleWarehouseSync: Failed to log unpublished products: %s", e)

        # Update the log record
        log.write({
            "status": "success",
            "total_api_products": total_api,
            "matched_products": matched,
            "updated_products": updated,
            "missing_products": missing,
            "message": "\n".join(errors) if errors else False,
        })

        _logger.info(
            "SimpleWarehouseSync: Sync finished. API=%s, matched=%s, updated=%s, missing=%s",
            total_api, matched, updated, missing,
        )

        # Send email notification
        self._send_sync_email_report(log, errors)

        return True

    def _send_sync_email_report(self, log, errors):
        """Send email report after sync completion."""
        try:
            Line = self.env["simple.warehouse.sync.line"].sudo()
            domain_base = [("log_id", "=", log.id)]
            
            total_synced = Line.search_count(domain_base + [("missing", "=", False)])
            unpublished_count = Line.search_count(domain_base + [("unpublished", "=", True)])
            published_count = max(total_synced - unpublished_count, 0)
            low_stock_count = Line.search_count(domain_base + [("low_stock", "=", True), ("missing", "=", False)])
            high_stock_count = Line.search_count(domain_base + [("high_stock", "=", True), ("missing", "=", False)])
            priority_missing_count = Line.search_count(
                domain_base + [("missing", "=", True), ("priority_brand", "=", True)]
            )

            # Prepare candle chart heights
            max_candle = max(log.total_api_products or 0, log.matched_products or 0, log.updated_products or 0, log.missing_products or 0) or 1
            api_height = int(30 + 70 * (log.total_api_products / max_candle)) if log.total_api_products else 30
            matched_height = int(30 + 70 * (log.matched_products / max_candle)) if log.matched_products else 30
            updated_height = int(30 + 70 * (log.updated_products / max_candle)) if log.updated_products else 30
            missing_height = int(30 + 70 * (log.missing_products / max_candle)) if log.missing_products else 30

            Mail = self.env['mail.mail'].sudo()
            subject = f"SyncVision Report - {fields.Datetime.now().strftime('%Y-%m-%d %H:%M')}"

            body = f"""
                <div style="background:#0b1225;padding:24px 0;color:#e5e7eb;">
                  <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" width="100%" style="max-width:640px;margin:0 auto;font-family:'Segoe UI','Helvetica Neue',Arial,sans-serif;">
                    <tr>
                      <td style="padding:0 16px;">
                        <table role="presentation" width="100%" style="border-collapse:collapse;background:#0f172a;border:1px solid #111827;border-radius:18px;box-shadow:0 14px 30px rgba(0,0,0,.45);">
                          <tr>
                            <td style="padding:18px 18px 10px 18px;">
                              <table role="presentation" width="100%" style="border-collapse:collapse;">
                                <tr>
                                  <td style="font-size:22px;font-weight:800;color:#f8fafc;">SyncVision &mdash; Report</td>
                                  <td style="font-size:12px;color:#94a3b8;text-align:right;">
                                    <div>Last sync</div>
                                    <div style="font-weight:600;color:#e5e7eb;">{(log.create_date or fields.Datetime.now()).strftime('%Y-%m-%d %H:%M')}</div>
                                  </td>
                                </tr>
                                <tr>
                                  <td colspan="2" style="font-size:13px;color:#94a3b8;padding-top:4px;">Summary of the latest sync between Connect API and Odoo inventory.</td>
                                </tr>
                              </table>
                            </td>
                          </tr>

                          <tr>
                            <td style="padding:0 18px 12px 18px;">
                              <table role="presentation" width="100%" style="border-collapse:collapse;border:1px solid #111827;border-radius:14px;overflow:hidden;background:#0b1225;">
                                <tr>
                                  <td style="padding:16px;text-align:center;">
                                    <div style="font-size:12px;color:#94a3b8;letter-spacing:.12em;text-transform:uppercase;">Total Synced</div>
                                    <div style="font-size:36px;font-weight:800;line-height:1;margin-top:6px;">{total_synced}</div>
                                    <div style="font-size:12px;color:#94a3b8;margin-top:4px;">products in last sync</div>
                                  </td>
                                </tr>
                              </table>
                            </td>
                          </tr>

                          <tr>
                            <td style="padding:0 18px 12px 18px;">
                              <table role="presentation" width="100%" style="border-collapse:collapse;border:1px solid #111827;border-radius:14px;background:#0b1225;">
                                <tr>
                                  <td colspan="2" style="padding:10px 14px;font-size:12px;color:#94a3b8;letter-spacing:.12em;text-transform:uppercase;border-bottom:1px solid #111827;font-weight:600;">Breakdown</td>
                                </tr>
                                <tr>
                                  <td style="padding:8px 14px;">
                                    <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#34d399;margin-right:6px;"></span>
                                    Published
                                  </td>
                                  <td style="padding:8px 14px;text-align:right;font-weight:700;">{published_count}</td>
                                </tr>
                                <tr>
                                  <td style="padding:8px 14px;">
                                    <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#f97316;margin-right:6px;"></span>
                                    Unpublished
                                  </td>
                                  <td style="padding:8px 14px;text-align:right;font-weight:700;">{unpublished_count}</td>
                                </tr>
                                <tr>
                                  <td style="padding:8px 14px;">
                                    <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#facc15;margin-right:6px;"></span>
                                    Low stock
                                  </td>
                                  <td style="padding:8px 14px;text-align:right;font-weight:700;">{low_stock_count}</td>
                                </tr>
                                <tr>
                                  <td style="padding:8px 14px;">
                                    <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#60a5fa;margin-right:6px;"></span>
                                    High stock
                                  </td>
                                  <td style="padding:8px 14px;text-align:right;font-weight:700;">{high_stock_count}</td>
                                </tr>
                                <tr>
                                  <td style="padding:8px 14px;">
                                    <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#ff6b6b;margin-right:6px;"></span>
                                    Missing (priority brands)
                                  </td>
                                  <td style="padding:8px 14px;text-align:right;font-weight:700;">{priority_missing_count}</td>
                                </tr>
                              </table>
                            </td>
                          </tr>

                          <tr>
                            <td style="padding:0 18px 12px 18px;">
                              <table role="presentation" width="100%" style="border-collapse:collapse;border:1px solid #111827;border-radius:14px;background:#0f172a;">
                                <tr style="background:#0b1225;">
                                  <td colspan="2" style="padding:10px 14px;font-weight:600;font-size:12px;color:#e5e7eb;">Quick summary</td>
                                </tr>
                                <tr>
                                  <td style="padding:8px 14px;color:#94a3b8;width:180px;">Database</td>
                                  <td style="padding:8px 14px;color:#e5e7eb;">{self.env.cr.dbname}</td>
                                </tr>
                                <tr>
                                  <td style="padding:8px 14px;color:#94a3b8;width:180px;">Sync Status</td>
                                  <td style="padding:8px 14px;color:#e5e7eb;">
                                    <span style="color:#22c55e;font-weight:700;">{log.status.capitalize()}</span>
                                    <span style="color:#94a3b8;"> &mdash; {"0 errors" if not errors else f"{len(errors)} errors"}</span>
                                  </td>
                                </tr>
                              </table>
                            </td>
                          </tr>

                          <tr>
                            <td style="padding:0 18px 12px 18px;">
                              <table role="presentation" width="100%" style="border-collapse:collapse;border:1px solid #111827;border-radius:14px;background:#0b1225;">
                                <tr>
                                  <td colspan="4" style="padding:12px 14px;font-size:12px;color:#94a3b8;letter-spacing:.12em;text-transform:uppercase;font-weight:600;">Candle chart (relative volume)</td>
                                </tr>
                                <tr>
                                  <td align="center" style="padding:12px 6px 16px 6px;">
                                    <div style="height:120px;width:18px;border-radius:12px;background:#1f2937;position:relative;overflow:hidden;">
                                      <div style="position:absolute;bottom:0;left:0;width:18px;height:{api_height}px;background:#22c55e;"></div>
                                    </div>
                                    <div style="margin-top:6px;font-size:11px;color:#94a3b8;line-height:1.4;">API</div>
                                  </td>
                                  <td align="center" style="padding:12px 6px 16px 6px;">
                                    <div style="height:120px;width:18px;border-radius:12px;background:#1f2937;position:relative;overflow:hidden;">
                                      <div style="position:absolute;bottom:0;left:0;width:18px;height:{matched_height}px;background:#f97316;"></div>
                                    </div>
                                    <div style="margin-top:6px;font-size:11px;color:#94a3b8;line-height:1.4;">Matched</div>
                                  </td>
                                  <td align="center" style="padding:12px 6px 16px 6px;">
                                    <div style="height:120px;width:18px;border-radius:12px;background:#1f2937;position:relative;overflow:hidden;">
                                      <div style="position:absolute;bottom:0;left:0;width:18px;height:{updated_height}px;background:#eab308;"></div>
                                    </div>
                                    <div style="margin-top:6px;font-size:11px;color:#94a3b8;line-height:1.4;">Updated</div>
                                  </td>
                                  <td align="center" style="padding:12px 6px 16px 6px;">
                                    <div style="height:120px;width:18px;border-radius:12px;background:#1f2937;position:relative;overflow:hidden;">
                                      <div style="position:absolute;bottom:0;left:0;width:18px;height:{missing_height}px;background:#f87171;"></div>
                                    </div>
                                    <div style="margin-top:6px;font-size:11px;color:#94a3b8;line-height:1.4;">Missing</div>
                                  </td>
                                </tr>
                              </table>
                            </td>
                          </tr>

                          <tr>
                            <td style="padding:0 18px 18px 18px;">
                              <table role="presentation" width="100%" style="border-collapse:collapse;border:1px solid #111827;border-radius:12px;background:#0f172a;">
                                <tr>
                                  <td style="padding:12px 14px;font-size:12px;color:#cbd5e1;line-height:1.6;">
                                    <div><strong>Status:</strong> {log.status}</div>
                                    <div><strong>API Products:</strong> {log.total_api_products}</div>
                                    <div><strong>Matched Products:</strong> {log.matched_products}</div>
                                    <div><strong>Updated Products:</strong> {log.updated_products}</div>
                                    <div><strong>Missing in Odoo:</strong> {log.missing_products}</div>
                                  </td>
                                </tr>
                              </table>
                            </td>
                          </tr>
                        </table>
                      </td>
                    </tr>
                  </table>
                </div>
            """

            blob = self._get_settings_blob()
            icp = self.env["ir.config_parameter"].sudo()
            raw_to = blob.get("email_to") or icp.get_param("simple_warehouse_sync.email_to") or ""
            recipients = [addr.strip() for addr in raw_to.replace(";", ",").split(",") if addr.strip()]
            
            if not recipients:
                fallback = [addr for addr in [self.env.user.email, self.env.company.email] if addr]
                recipients = fallback

            if recipients:
                mail_values = {
                    'subject': subject,
                    'body_html': body,
                    'email_to': ",".join(recipients),
                    'email_from': self.env.user.email or self.env.company.email or 'noreply@example.com',
                }
                mail = Mail.create(mail_values)
                mail.send()
            else:
                _logger.info("SimpleWarehouseSync: No notification recipients configured; email skipped")
        except Exception as e:
            _logger.error("SimpleWarehouseSync: Failed to send email report: %s", e)

    def action_run_sync(self):
        """Button entry point: run sync and show a notification in the UI."""
        self.ensure_one()
        self._persist_settings_from_form()
        ok = self.run_sync()

        status = "success" if ok else "danger"
        msg = "Warehouse sync completed successfully" if ok else "Warehouse sync failed"

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "SyncVision",
                "message": msg,
                "type": status,
                "sticky": False,
            },
        }

    def action_refresh_panel(self):
        """Reload the dashboard without running the sync again."""
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }

    # ==== Report openers for latest run ====

    def _get_latest_log(self):
        return self.env["simple.warehouse.sync.log"].search([], order="create_date desc", limit=1)

    def _open_lines_with_domain(self, domain, name, action_xmlid="simple_warehouse_sync.action_simple_warehouse_sync_lines", search_view_xmlid=None, extra_context=None):
        action = self.env.ref(action_xmlid).read()[0]
        action["name"] = name
        action["domain"] = domain

        if search_view_xmlid:
            search_view = self.env.ref(search_view_xmlid, raise_if_not_found=False)
            if search_view:
                action["search_view_id"] = search_view.id

        ctx = action.get("context") or {}
        if isinstance(ctx, str):
            ctx = safe_eval(ctx)
        if extra_context:
            ctx.update(extra_context)
        action["context"] = ctx

        return action

    def action_view_all_lines(self):
        log = self._get_latest_log()
        if not log:
            return False
        domain = [["log_id", "=", log.id], ["missing", "=", False]]
        return self._open_lines_with_domain(domain, "All Synced Products")

    def action_view_high_stock(self):
        log = self._get_latest_log()
        if not log:
            return False
        domain = [["log_id", "=", log.id], ["high_stock", "=", True]]
        return self._open_lines_with_domain(domain, "High Stock (> 5)")

    def action_view_low_stock(self):
        log = self._get_latest_log()
        if not log:
            return False
        domain = [["log_id", "=", log.id], ["low_stock", "=", True]]
        return self._open_lines_with_domain(domain, "Low Stock (< 5)")

    def action_view_unpublished(self):
        log = self._get_latest_log()
        if not log:
            return False
        domain = [["log_id", "=", log.id], ["unpublished", "=", True]]
        return self._open_lines_with_domain(domain, "Unpublished Products")

    def action_view_missing(self):
        log = self._get_latest_log()
        if not log:
            return False
        domain = [["log_id", "=", log.id], ["missing", "=", True]]
        extra_context = {
            "search_default_priority_brands_qty50": 1,
        }
        return self._open_lines_with_domain(
            domain,
            "Missing Products (API Only)",
            action_xmlid="simple_warehouse_sync.action_simple_warehouse_sync_lines_missing",
            search_view_xmlid="simple_warehouse_sync.view_simple_missing_products_search",
            extra_context=extra_context,
        )


class SimpleWarehouseSyncLog(models.Model):
    _name = "simple.warehouse.sync.log"
    _description = "Simple Warehouse Sync Log"
    _order = "create_date desc"

    create_date = fields.Datetime("Date", readonly=True)
    status = fields.Selection([
        ("success", "Success"),
        ("fail", "Fail"),
    ], default="success", readonly=True)
    total_api_products = fields.Integer("API Products", readonly=True)
    matched_products = fields.Integer("Matched Products", readonly=True)
    updated_products = fields.Integer("Updated Products", readonly=True)
    missing_products = fields.Integer("Missing in Odoo", readonly=True)
    message = fields.Text("Details")
    api_status_code = fields.Integer("API Status Code", readonly=True)
    api_latency_ms = fields.Integer("API Latency (ms)", readonly=True)


class SimpleWarehouseSyncLine(models.Model):
    """Per-product log line for the last sync run only."""

    _name = "simple.warehouse.sync.line"
    _description = "Simple Warehouse Sync Line"
    _order = "create_date desc, id desc"

    create_date = fields.Datetime("Sync Date", readonly=True)
    log_id = fields.Many2one("simple.warehouse.sync.log", string="Sync Log", ondelete="cascade", index=True)

    # Product info
    product_id = fields.Many2one("product.product", string="Product", readonly=True, index=True)
    sku = fields.Char("SKU", readonly=True, index=True)
    brand = fields.Char("Brand", readonly=True)
    external_id = fields.Char("External ID", readonly=True)
    barcode = fields.Char("Barcode", readonly=True)

    # Quantities
    api_qty = fields.Float("API Quantity", readonly=True)
    qty = fields.Float("Quantity", readonly=True)

    # Status flags
    low_stock = fields.Boolean("Low Stock Alert", readonly=True, index=True)
    high_stock = fields.Boolean("High Stock", readonly=True, index=True)
    unpublished = fields.Boolean("Unpublished", readonly=True, index=True)
    missing = fields.Boolean("Missing Product", readonly=True, index=True)
    priority_brand = fields.Boolean("Priority Brand", readonly=True, index=True)

    note = fields.Text("Status Note", readonly=True)
