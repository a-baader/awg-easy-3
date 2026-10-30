"""Данные backup не дают разрешения исполнять произвольный Docker-образ."""
import re

import vpnctl_boot
from vpnctl_common import ManagerError


def select(manager, manifest, previous, selected, trust_backup_images=False):
    release=None
    if not previous and not trust_backup_images:
        try:
            release=vpnctl_boot.fetch_manifest((manager.project/'VERSION').read_text().strip())
        except Exception as error:
            raise ManagerError('Не удалось проверить образы своего релиза для fresh restore',69) from error
    result={}
    for name in selected:
        if trust_backup_images:
            source=manifest['manager_config']['components'][name]
            image=source['image']
            if not isinstance(image,str) or not re.fullmatch(r'(?:sha256:[a-f0-9]{64}|[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[a-f0-9]{64})',image):
                raise ManagerError('Для доверенного образа backup требуется неизменяемый ID или digest',65)
            result[name]={'image':image,'image_ref':image,'version':source.get('version',manifest['manager_config']['version'])}
        elif previous:
            installed=previous['components'].get(name)
            if not installed:raise ManagerError('Для частичного restore сначала установите выбранный компонент',65)
            result[name]={key:installed[key] for key in ('image','image_ref') if key in installed}
            result[name]['version']=installed.get('version',previous['version'])
        else:
            image=release['images'][name]
            result[name]={'image':image,'image_ref':image,'version':release['version']}
    return result
