import sys,os,types,tempfile,threading,shutil,time,io
from pathlib import Path
os.environ['RELAY_UPLOAD_KEY']='s' * 32
os.environ['RELAY_PUBLIC_BASE_URL']='https://lecture-bridge.up.railway.app'
os.environ['RELAY_DATA_DIR']=tempfile.mkdtemp(prefix='relay_mock_')
request=types.SimpleNamespace(headers={},content_length=None,path='',get_json=lambda silent=True:None,stream=None)
flask=types.ModuleType('flask');flask.request=request
flask.jsonify=lambda *args, **kw: args[0] if args else kw
flask.send_file=lambda *args,**kwargs: {'file':str(args[0]),'download_name':kwargs['download_name']}
sys.modules['flask']=flask
sys.path.insert(0,'/mnt/data/Lecture_Bridge_Standalone')
import relay_api
class App:
 def __init__(self): self.routes={}
 def post(self,path):
  def decorate(f): self.routes[path]=f;return f
  return decorate
 def get(self,path):
  def decorate(f): self.routes[path]=f;return f
  return decorate
 def route(self,path,methods=None):
  def decorate(f): self.routes[path]=f;return f
  return decorate
 def after_request(self,f):return f
app=App();relay_api.install_relay_routes(app)
request.headers={'X-Relay-Key':'s'*32,'Origin':'https://vidcloud.eu.org'}
request.get_json=lambda silent=True:{'segments':1}
start=app.routes['/api/relay/start'](); assert start[1] == 201
job_id=start[0]['job_id']; print('Created job',job_id[:7])
blob=Path('/mnt/data/lecture_bot_work/hls_mock/clip_00.pdf').read_bytes()
request.stream=io.BytesIO(blob);request.content_length=len(blob)
upload=app.routes['/api/relay/<job_id>/segment/<int:index>'](job_id,0)
assert upload['received']==1,upload
fin=app.routes['/api/relay/<job_id>/finish'](job_id)
assert fin[1]==202
for i in range(100):
 if relay_api._jobs[job_id]['status']!='processing':break
 time.sleep(.1)
state=app.routes['/api/relay/<job_id>/status'](job_id)
print('Converted status',state['status'], 'size',state.get('bytes_output'))
assert state['status']=='ready',state
assert state['download_url'].startswith('https://lecture-bridge.up.railway.app/relay-download/')
assert state['download_url'].endswith('.mp4')
token=relay_api._jobs[job_id]['download_token']
download=app.routes['/relay-download/<job_id>/<token>.mp4'](job_id,token)
assert download['download_name']=='lecture.mp4', download
request.headers={'X-Relay-Key':'wrong-key'}
assert app.routes['/api/relay/<job_id>/status'](job_id)[1] == 401
print('Mock API + FFmpeg + download URL: PASS')
shutil.rmtree(os.environ['RELAY_DATA_DIR'],ignore_errors=True)
