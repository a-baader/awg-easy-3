"""Проверяет OS, Node.js и Go-зависимости образов закреплённым Trivy."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request

VERSION = '0.76.1'
ARCHIVE_SHA256 = 'c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', nargs='+', help='Имена или digest образов')
    parser.add_argument('--output', type=Path, default=Path('security-report'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wg-dual-security-') as directory:
        directory = Path(directory)
        url = f'https://github.com/aquasecurity/trivy/releases/download/v{VERSION}/trivy_{VERSION}_Linux-64bit.tar.gz'
        archive = urllib.request.urlopen(url, timeout=120).read()
        if hashlib.sha256(archive).hexdigest() != ARCHIVE_SHA256:
            raise RuntimeError('Контрольная сумма Trivy не совпала')
        archive_path = directory / 'trivy.tar.gz'
        archive_path.write_bytes(archive)
        with tarfile.open(archive_path) as package:
            binary = package.extractfile('trivy').read()
        executable = directory / 'trivy'
        executable.write_bytes(binary)
        executable.chmod(0o700)
        results = []
        for index, image in enumerate(args.images):
            report = args.output / f'image-{index}.json'
            subprocess.run([str(executable), 'image', '--scanners', 'vuln',
                '--format', 'json', '--output', str(report), '--timeout', '15m', image], check=True)
            data = json.loads(report.read_text())
            findings = [v for result in data.get('Results', [])
                for v in result.get('Vulnerabilities', [])
                if v.get('Severity') in ('HIGH', 'CRITICAL')]
            results.append({'image': image, 'high_critical': len(findings), 'report': report.name})
            print(f'{image}: HIGH/CRITICAL — {len(findings)}', flush=True)
        (args.output / 'summary.json').write_text(json.dumps(results, indent=2) + '\n')
        return int(any(item['high_critical'] for item in results))


if __name__ == '__main__':
    raise SystemExit(main())
