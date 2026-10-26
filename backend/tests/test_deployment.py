from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.deployment import protect_public_demo

def client():
    app=FastAPI()
    app.middleware('http')(protect_public_demo)
    @app.api_route('/api/example',methods=['GET','POST','PUT','PATCH','DELETE'])
    def example():return {'ok':True}
    return TestClient(app)

def test_public_reads_and_security_headers(monkeypatch):
    monkeypatch.setenv('MLB_PUBLIC_DEMO','1')
    response=client().get('/api/example')
    assert response.status_code==200
    assert response.headers['X-Frame-Options']=='DENY'
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']

def test_public_mutations_never_reach_handlers(monkeypatch):
    monkeypatch.setenv('MLB_PUBLIC_DEMO','1')
    for method in ('POST','PUT','PATCH','DELETE'):
        response=client().request(method,'/api/example')
        assert response.status_code==403
        assert 'read-only' in response.json()['detail']

def test_local_workspace_remains_editable(monkeypatch):
    monkeypatch.delenv('MLB_PUBLIC_DEMO',raising=False)
    assert client().post('/api/example').status_code==200


def test_real_application_import_routes_are_protected(monkeypatch):
    monkeypatch.setenv('MLB_PUBLIC_DEMO','1')
    from app.main import app
    with TestClient(app) as browser:
        for path in ('/api/imports','/api/workspace/refresh','/api/contracts/import','/api/contracts/refresh'):
            assert browser.post(path,json={}).status_code==403
