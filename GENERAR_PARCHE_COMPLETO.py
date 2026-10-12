"""Genera el ZIP de archivos COMPLETOS desde el GitHub público ImpactoAPP.

Uso: python GENERAR_PARCHE_COMPLETO.py
No modifica GitHub ni toca la base de datos.
"""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import zipfile
from urllib.request import Request, urlopen
import sys

PACKAGE=Path(__file__).resolve().parent
REPO='impactomedia/ImpactoAPP'


def download(url):
    req=Request(url,headers={'User-Agent':'Nexora-safe-patch-builder','Accept':'application/json'})
    with urlopen(req,timeout=30) as resp:
        return resp.read()


def main():
    from scripts.aplicar_roles_nexora import PATCHES
    source_sha=json.loads(download(f'https://api.github.com/repos/{REPO}/commits/main').decode('utf-8'))['sha']
    print('Leyendo versión original desde GitHub:',source_sha[:12])
    with tempfile.TemporaryDirectory(prefix='nexora_patch_') as temp:
        repo=Path(temp)
        for name in list(PATCHES)+['app/templates/hr/collaborators.html']:
            print(' -',name)
            data=download(f'https://raw.githubusercontent.com/{REPO}/{source_sha}/{name}')
            original=data.decode('utf-8')
            p=repo/name
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(original,encoding='utf-8')
        from scripts.aplicar_roles_nexora import PATCHES
        outputs={}
        for name,handler in PATCHES.items():
            outputs[name]=handler((repo/name).read_text(encoding='utf-8'))
        outputs['app/templates/hr/collaborators.html']=(PACKAGE/'roles_payload/hr_collaborators.html').read_text(encoding='utf-8')
        outputs['tests/test_developer_role_v125.py']=(PACKAGE/'tests/test_developer_role_v125.py').read_text(encoding='utf-8')
        out=PACKAGE/'ImpactoAPP_Roles_Desarrollador_v1.25.0_Archivos_Completos.zip'
        with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for path,content in outputs.items():
                z.writestr(path,content)
            z.writestr('LEEME_ROLES_v1.25.0.txt', (PACKAGE/'LEEME.txt').read_text(encoding='utf-8')+f'\nSHA BASE CONSULTADO: {source_sha}\n')
        print('\nArchivos generados:',len(outputs))
        print('ZIP listo para cargar a GitHub:',out)
        print('No se han modificado ni desplegado archivos en GitHub.')

if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('ERROR. No se generó un parche publicable:',exc,file=sys.stderr)
        raise SystemExit(2)
