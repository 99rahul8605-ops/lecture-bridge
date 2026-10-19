"""Separate authenticated metadata-only endpoints installed on existing Flask app."""
import hmac
import os
from flask import jsonify, request
from auto_catalog import init_db, ingest, command, mark_delivered


def install_catalog_routes(app):
    init_db()
    origin=os.environ.get('RELAY_ALLOWED_ORIGIN','https://vidcloud.eu.org').rstrip('/')

    def auth():
        key=os.environ.get('CATALOG_SYNC_KEY','')
        supplied=request.headers.get('X-Catalog-Key','')
        return len(key)>=24 and hmac.compare_digest(key,supplied)

    @app.after_request
    def catalog_cors(response):
        if request.path.startswith('/api/catalog/') and request.headers.get('Origin')==origin:
            response.headers['Access-Control-Allow-Origin']=origin
            response.headers['Access-Control-Allow-Headers']='Content-Type, X-Catalog-Key'
            response.headers['Access-Control-Allow-Methods']='POST,OPTIONS'
            response.headers['Vary']='Origin'
        return response

    @app.route('/api/catalog/<path:tail>',methods=['OPTIONS'])
    def options(tail):
        return ('',204) if request.headers.get('Origin')==origin else (jsonify(error='Origin denied'),403)

    @app.post('/api/catalog/sync')
    def sync():
        if not auth():return jsonify(error='Bad catalog key'),401
        if request.content_length and request.content_length>300000:return jsonify(error='Payload too large'),413
        try:return jsonify(success=True,**ingest(request.get_json(silent=True)))
        except (ValueError,TypeError) as exc:return jsonify(error=str(exc)),400

    @app.post('/api/catalog/command')
    def cmd():
        if not auth():return jsonify(error='Bad catalog key'),401
        body=request.get_json(silent=True) or {}
        uid=body.get('uid')
        try: uid=int(uid)
        except (ValueError,TypeError):return jsonify(error='Invalid user'),400
        owner=os.environ.get('OWNER_ID','').strip()
        if owner and str(uid)!=owner:return jsonify(error='Not owner'),403
        try:return jsonify(message=command(uid,body.get('command'),body.get('arg','')))
        except (ValueError,OverflowError,TypeError) as exc:return jsonify(error=str(exc)),400

    @app.post('/api/catalog/delivered')
    def delivered():
        if not auth():return jsonify(error='Bad catalog key'),401
        body=request.get_json(silent=True) or {}
        try:return jsonify(updated=mark_delivered(body.get('videoId')))
        except ValueError as exc:return jsonify(error=str(exc)),400
