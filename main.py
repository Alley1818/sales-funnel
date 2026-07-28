"""
Sales Funnel — main entry point.
Provides CLI for manual operations and starts the API server.
"""
import os
import sys
import json
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))


def cli():
    """Command-line interface."""
    if len(sys.argv) < 2:
        print("Sales Funnel CLI")
        print("Usage:")
        print("  python main.py serve              - Start API server")
        print("  python main.py import <file.xlsx> - Import leads from Excel")
        print("  python main.py report             - Show funnel report")
        print("  python main.py batch [industry]   - Show next leads to call")
        print("  python main.py wa-status          - Check WhatsApp connection")
        print("  python main.py wa-connect         - Initialize WhatsApp")
        print("  python main.py wa-qr              - Get WhatsApp QR code")
        return

    cmd = sys.argv[1]

    if cmd == "serve":
        from app import create_app
        app = create_app()
        port = int(os.environ.get("PORT", "5050"))

        try:
            import gunicorn.app.base

            class StandaloneApplication(gunicorn.app.base.BaseApplication):
                def __init__(self, app, options=None):
                    self.options = options or {}
                    self.application = app
                    super().__init__()

                def load_config(self):
                    for key, value in self.options.items():
                        if key in self.cfg.settings and value is not None:
                            self.cfg.set(key.lower(), value)

                def load(self):
                    return self.application

            StandaloneApplication(app, {
                "bind": f"0.0.0.0:{port}",
                "workers": int(os.environ.get("GUNICORN_WORKERS", "2")),
                "timeout": 120,
            }).run()
        except ImportError:
            import logging
            logging.getLogger("sales_funnel").warning(
                "gunicorn not installed — falling back to Flask dev server"
            )
            app.run(host="0.0.0.0", port=port, debug=False)

    elif cmd == "import":
        from leads_db import import_excel
        path = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("DEFAULT_EXCEL_PATH", "")
        if not path:
            print("Error: No Excel file specified. Pass as argument or set DEFAULT_EXCEL_PATH env var.")
            return
        count = import_excel(path)
        print(f"Imported {count} leads")

    elif cmd == "report":
        from db_conn import get_conn
        from funnel_engine import FunnelEngine
        eng = FunnelEngine(get_conn())
        print(eng.get_funnel_report())

    elif cmd == "batch":
        from db_conn import get_conn
        from funnel_engine import FunnelEngine
        eng = FunnelEngine(get_conn())
        industry = sys.argv[2] if len(sys.argv) > 2 else None
        leads = eng.run_batch(industry=industry)
        print(f"Leads to call ({len(leads)}):")
        for l in leads:
            print(f"  [{l['id']}] {l['company_name']}")
            print(f"        {l['mobile']} | {l['email']} | {l['industry']}")

    elif cmd == "wa-status":
        from whatsapp_client import WhatsAppClient
        client = WhatsAppClient()
        state = client.get_connection_state()
        print(f"WhatsApp state: {state}")

    elif cmd == "wa-connect":
        from whatsapp_client import WhatsAppClient
        client = WhatsAppClient()
        result = client.create_instance()
        print(f"Instance created: {json.dumps(result, indent=2, ensure_ascii=False)}")

    elif cmd == "wa-qr":
        from whatsapp_client import WhatsAppClient
        client = WhatsAppClient()
        qr = client.get_qr_code()
        state = client.get_connection_state()
        print(f"State: {state}")
        if qr:
            print(f"QR (base64): {qr[:100]}...")
        else:
            print("No QR code available. Try wa-connect first.")

    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    cli()
