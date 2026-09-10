from app import app
import uuid

client = app.test_client()

home = client.get('/')
print('HOME', home.status_code, 'audioTranslateBtn' in home.get_data(as_text=True), 'historySearch' in home.get_data(as_text=True))

login = client.get('/login')
print('LOGIN', login.status_code, 'loginTab' in login.get_data(as_text=True), 'registerTab' in login.get_data(as_text=True))

dash = client.get('/dashboard')
print('DASHBOARD_REDIRECT', dash.status_code, dash.headers.get('Location'))

username = f'user_{uuid.uuid4().hex[:8]}'
password = 'secret123'
reg = client.post('/api/auth/register', json={'username': username, 'password': password})
print('REGISTER', reg.status_code, reg.get_json())

text = client.post('/api/translate/text', json={'text': 'hello', 'target_lang': 'fr'})
print('TEXT', text.status_code, text.get_json())

hist = client.get('/api/history')
print('HISTORY', hist.status_code, len(hist.get_json()['history']))

items = hist.get_json()['history']
if items:
    del_resp = client.delete(f"/api/history/{items[0]['id']}")
    print('DELETE', del_resp.status_code, del_resp.get_json())
else:
    print('DELETE', 'NONE', None)
