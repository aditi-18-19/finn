from fastapi.testclient import TestClient
from api import main

client = TestClient(main.app)


def test_health():
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'


def test_feed_reads_existing_json(monkeypatch):
    monkeypatch.setattr(main, 'load_json', lambda name, default=None: {
        'schema_version': '1.0', 'generated_at': '2026-11-15T00:00:00',
        'summary': {}, 'items': [
            {'nse_symbol': 'RELIANCE', 'date_time': '2026-11-15T00:00:00', 'priority_score': 4}
        ]
    })
    response = client.get('/feed?hours=24')
    assert response.status_code == 200
    assert response.json()['count'] == 1


def test_alert_filter(monkeypatch):
    monkeypatch.setattr(main, 'load_json', lambda name, default=None: {
        'alerts': [
            {'symbol': 'RELIANCE', 'type': 'material_announcement', 'timestamp': '2026-11-15T00:00:00'},
            {'symbol': 'TCS', 'type': 'market_anomaly', 'timestamp': '2026-11-15T00:00:00'},
        ], 'summary': {}, 'config': {}
    })
    response = client.get('/alerts?symbol=RELIANCE')
    assert response.status_code == 200
    assert response.json()['count'] == 1
    assert response.json()['alerts'][0]['symbol'] == 'RELIANCE'


def test_company_unknown_symbol(monkeypatch):
    monkeypatch.setattr(main, 'company_universe', lambda: {})
    monkeypatch.setattr(main, 'load_json', lambda name, default=None: {} if name == 'prices.json' else {'items': [], 'alerts': [], 'results': [], 'cards': []})
    response = client.get('/company/NOTREAL')
    assert response.status_code == 404
