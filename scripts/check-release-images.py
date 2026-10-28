"""Не допускает публикацию релиза с недоступными анонимно GHCR-образами."""
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlencode
from urllib.request import Request,urlopen

manifest=json.loads(Path(sys.argv[1]).read_text())
for backend,reference in manifest['images'].items():
    repository,digest=reference.removeprefix('ghcr.io/').split('@')
    try:
        with urlopen('https://ghcr.io/token?'+urlencode({'service':'ghcr.io','scope':'repository:'+repository+':pull'}),timeout=30) as response:
            token=json.load(response)['token']
        request=Request('https://ghcr.io/v2/'+repository+'/manifests/'+digest,headers={
            'Authorization':'Bearer '+token,
            'Accept':'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'})
        with urlopen(request,timeout=30) as response:
            data=response.read();actual=response.headers.get('Docker-Content-Digest')
        if actual!=digest or 'sha256:'+hashlib.sha256(data).hexdigest()!=digest:raise ValueError('digest')
    except Exception:
        raise SystemExit('Ошибка: '+backend.upper()+' недоступен анонимно или его digest неверен. В настройках пакета '+repository+' установите Public и повторите задачу публикации. Релиз пока не создан.') from None
    print(backend.upper()+': анонимный доступ и digest подтверждены')
