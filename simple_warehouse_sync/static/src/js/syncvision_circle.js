// Plain JS (no Odoo module import) to update the SyncVision circle ring based on legend numbers
(function () {
    function getNumber(id) {
        var el = document.getElementById(id);
        if (!el) {
            return 0;
        }
        var txt = (el.textContent || "").replace(/,/g, "").trim();
        var val = parseFloat(txt || "0");
        return isNaN(val) ? 0 : val;
    }

    function updateCircleRing() {
        var dashboard = document.querySelector(".simple_sync_dashboard");
        if (!dashboard) {
            return false;
        }
        var ring = dashboard.querySelector(".circle-ring");
        if (!ring) {
            return false;
        }

        var published = getNumber("sv_pub");
        var unpub = getNumber("sv_unpub");
        var low = getNumber("sv_low");
        var high = getNumber("sv_high");
        var missing = getNumber("sv_missing");

        var total = published + unpub + low + high + missing;
        if (!total) {
            return false;
        }

        var segments = [
            ["#34d399", published],   // Published
            ["#f97316", unpub],       // Unpublished
            ["#facc15", low],         // Low stock
            ["#60a5fa", high],        // High stock
            ["#ff6b6b", missing],     // Missing (priority)
        ].filter(function (seg) { return seg[1] > 0; });

        if (!segments.length) {
            return false;
        }

        var start = 0;
        var parts = [];
        segments.forEach(function (seg) {
            var color = seg[0];
            var value = seg[1];
            var angle = (value / total) * 360;
            var end = start + angle;
            parts.push(color + " " + start.toFixed(1) + "deg " + end.toFixed(1) + "deg");
            start = end;
        });

        var gradient = "conic-gradient(" + parts.join(",") + ")";
        console.debug("[SyncVision] circle data", { published: published, unpub: unpub, low: low, high: high, missing: missing, total: total, gradient: gradient });
        ring.style.background = gradient;
        return true;
    }

    function bootCircle() {
        var tries = 0;
        var maxTries = 20;
        var interval = setInterval(function () {
            tries++;
            if (updateCircleRing() || tries >= maxTries) {
                clearInterval(interval);
            }
        }, 250);
    }

    if (document.readyState === "complete" || document.readyState === "interactive") {
        bootCircle();
    } else {
        document.addEventListener("DOMContentLoaded", bootCircle);
    }

    // Debug helper
    window._syncvisionCircleDebug = {
        updateCircleRing: updateCircleRing,
    };
})();
