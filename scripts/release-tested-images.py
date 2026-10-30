"""Сохранение проверенных CI-образов и публикация тех же байтов без пересборки."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def run(*arguments):
    return subprocess.check_output(arguments, text=True).strip()


def verify(directory, commit):
    record=json.loads((directory/'manifest.json').read_text())
    if record.get('source_commit')!=commit or set(record.get('images',{}))!={'awg','wg'}:
        raise ValueError('CI-артефакт не соответствует commit релиза')
    if any(not re.fullmatch(r'sha256:[a-f0-9]{64}', value) for value in record['images'].values()):
        raise ValueError('Некорректный ID проверенного образа')
    if hashlib.sha256((directory/'images.tar').read_bytes()).hexdigest()!=record.get('archive_sha256'):
        raise ValueError('Контрольная сумма архива CI не совпала')
    return record


def save(directory, commit):
    directory.mkdir(parents=True,exist_ok=True)
    images={name:run('docker','image','inspect','-f','{{.Id}}','vpn-easy-'+name+':dual-stage7') for name in ['awg','wg']}
    subprocess.run(['docker','save','-o',str(directory/'images.tar'),*[images[name] for name in ['awg','wg']]],check=True)
    record={'source_commit':commit,'images':images,'archive_sha256':hashlib.sha256((directory/'images.tar').read_bytes()).hexdigest()}
    (directory/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')


def publish(directory, commit, repository, backend, version, output):
    if not re.fullmatch(r'[a-z0-9_.-]+/[a-z0-9_.-]+',repository) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-rc\.[1-9]\d*)?',version):
        raise ValueError('Некорректные параметры публикации')
    record=verify(directory,commit)
    reference='ghcr.io/'+repository+'-'+backend+':'+version
    exists=subprocess.run(['docker','manifest','inspect',reference],capture_output=True)
    if exists.returncode==0:
        raise ValueError('Версионный образ уже опубликован; перезапись запрещена')
    subprocess.run(['docker','load','-i',str(directory/'images.tar')],check=True)
    image=record['images'][backend]
    info=json.loads(run('docker','image','inspect',image))[0]
    if info['Id']!=image or info['Os']!='linux' or info['Architecture']!='amd64':
        raise ValueError('Загруженный образ отличается от проверенного CI')
    subprocess.run(['docker','tag',image,reference],check=True)
    pushed=run('docker','push',reference)
    digests=re.findall(r'digest: (sha256:[a-f0-9]{64})',pushed)
    if len(digests)!=1:
        raise ValueError('Registry не подтвердил единственный digest образа')
    output.write_text(json.dumps({'backend':backend,'digest':digests[0],'tested_image_id':image})+'\n')
    with open(os.environ['GITHUB_OUTPUT'],'a') as stream:
        stream.write('digest='+digests[0]+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Работа с проверенными образами CI')
    parser.add_argument('action',choices=['save','publish'])
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--repository');parser.add_argument('--backend',choices=['awg','wg'])
    parser.add_argument('--version');parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not re.fullmatch(r'[a-f0-9]{40}',args.commit):parser.error('Некорректный commit')
    if args.action=='save':save(args.directory,args.commit)
    else:publish(args.directory,args.commit,args.repository,args.backend,args.version,args.output)
