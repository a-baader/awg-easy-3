"""Продвижение default branch разрешено только после опубликованного stable."""
import argparse
import json
import re
import subprocess
from urllib.request import urlopen


def api(repository, endpoint, body=None):
    args=['gh','api','repos/'+repository+'/'+endpoint]
    if body is not None:args+=['--method','POST' if endpoint.startswith('statuses/') else 'PATCH','--input','-']
    return json.loads(subprocess.check_output(args,input=json.dumps(body) if body is not None else None,text=True))


def promote(repository,version,commit,dry_run=False):
    if repository!='a-baader/wg-dual-easy' or not re.fullmatch(r'\d+\.\d+\.\d+',version) or not re.fullmatch(r'[a-f0-9]{40}',commit):
        raise ValueError('Некорректные параметры stable-продвижения')
    release=api(repository,'releases/tags/v'+version)
    if release['draft'] or release['prerelease']:raise ValueError('Релиз ещё не опубликован как stable')
    with urlopen('https://github.com/'+repository+'/releases/download/v'+version+'/release-manifest.json',timeout=30) as response:
        raw=response.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('Манифест слишком велик')
    manifest=json.loads(raw)
    if manifest.get('version')!=version or manifest.get('source_commit')!=commit:
        raise ValueError('Опубликованный manifest не соответствует продвигаемому commit')
    branch=api(repository,'git/ref/heads/master')['object']['sha']
    subprocess.run(['git','fetch','--no-tags','origin',branch,commit],check=True)
    subprocess.run(['git','merge-base','--is-ancestor',branch,commit],check=True)
    if dry_run:return {'ready':True,'previous':branch,'candidate':commit}
    api(repository,'statuses/'+commit,{'state':'success','context':'stable-release',
        'description':'Stable-релиз и manifest опубликованы для этого commit','target_url':release['html_url']})
    if branch!=commit:api(repository,'git/refs/heads/master',{'sha':commit,'force':False})
    return {'promoted':branch!=commit,'commit':commit}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Продвинуть master после публикации stable-релиза')
    parser.add_argument('--repository',required=True);parser.add_argument('--version',required=True)
    parser.add_argument('--commit',required=True);parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args();print(json.dumps(promote(args.repository,args.version,args.commit,args.dry_run)))
