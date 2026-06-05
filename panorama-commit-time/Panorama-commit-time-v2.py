#!/usr/bin/env python3
"""
增强版：从 Panorama 获取“Commit to Panorama”任务，并将 Job ID、用户名、Enqueued、Dequeued、Completed、
Queue Time、Execution Time、Total Time 以追加方式写入 commit_time.txt；如果文件中已存在相同 Job ID，则跳过。

记录字段：
- job_id
- user
- enqueued
- dequeued
- completed
- queue_time        (dequeued - enqueued)
- execution_time    (completed - dequeued)
- total_time        (completed - enqueued)

说明：
- 只记录 Type == Commit 的 job（即 Commit to Panorama）
- 不记录 CommitAll
- 支持：用户名/密码自动 keygen，或直接传 API key
- 支持 Panorama XML API 返回结构化 XML 或 CLI 文本表格两种解析方式

用法：
python panorama_commit_jobs_enhanced.py --host <panorama_ip> --username <user> --password '<pwd>'
python panorama_commit_jobs_enhanced.py --host <panorama_ip> --api-key <API_KEY>
"""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set
import xml.etree.ElementTree as ET

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

API_TIMEOUT = 30
SHOW_JOBS_ALL_CMD = '<show><jobs><all></all></jobs></show>'
SHOW_JOB_ID_CMD_TEMPLATE = '<show><jobs><id>{job_id}</id></jobs></show>'


# -------------------- API helpers --------------------

def api_keygen(host: str, username: str, password: str, verify_ssl: bool) -> str:
    url = f'https://{host}/api/'
    params = {'type': 'keygen', 'user': username, 'password': password}
    r = requests.get(url, params=params, timeout=API_TIMEOUT, verify=verify_ssl)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    if root.get('status') != 'success':
        raise RuntimeError(f'API keygen failed: {r.text}')
    key_elem = root.find('.//key')
    if key_elem is None or not (key_elem.text or '').strip():
        raise RuntimeError(f'API key not found in response: {r.text}')
    return key_elem.text.strip()


def api_op(host: str, api_key: str, cmd_xml: str, verify_ssl: bool) -> ET.Element:
    url = f'https://{host}/api/'
    params = {'type': 'op', 'cmd': cmd_xml}
    headers = {'X-PAN-KEY': api_key}
    r = requests.post(url, params=params, headers=headers, timeout=API_TIMEOUT, verify=verify_ssl)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    if root.get('status') != 'success':
        raise RuntimeError(f'API op failed for cmd={cmd_xml!r}: {r.text}')
    return root


# -------------------- XML/text parsing helpers --------------------

def xml_text(elem: Optional[ET.Element]) -> str:
    return '' if elem is None or elem.text is None else elem.text.strip()


def recursive_find_first_text(root: ET.Element, tag_names: Iterable[str]) -> str:
    wanted = {t.lower() for t in tag_names}
    for elem in root.iter():
        if elem.tag.lower() in wanted and (elem.text or '').strip():
            return elem.text.strip()
    return ''


def recursive_find_job_nodes(root: ET.Element) -> List[ET.Element]:
    jobs = []
    for elem in root.iter():
        tag = elem.tag.lower()
        if tag == 'job':
            jobs.append(elem)
        elif tag == 'entry':
            child_tags = {child.tag.lower() for child in list(elem)}
            if 'id' in child_tags and ('type' in child_tags or 'status' in child_tags):
                jobs.append(elem)
    return jobs


def parse_jobs_all_from_xml(root: ET.Element) -> List[Dict[str, str]]:
    jobs: List[Dict[str, str]] = []
    for node in recursive_find_job_nodes(root):
        rec = {
            'job_id': xml_text(node.find('./id')),
            'type': xml_text(node.find('./type')),
            'enqueued': xml_text(node.find('./tenq')) or xml_text(node.find('./enqueued')),
            'dequeued': xml_text(node.find('./tdeq')) or xml_text(node.find('./dequeued')),
            'completed': xml_text(node.find('./tfin')) or xml_text(node.find('./completed')),
            'user': xml_text(node.find('./user')),
        }
        if rec['job_id'] and rec['type']:
            jobs.append(rec)
    return jobs


def extract_result_text(root: ET.Element) -> str:
    result_elem = root.find('.//result')
    if result_elem is None:
        return ''
    pieces: List[str] = []
    if result_elem.text and result_elem.text.strip():
        pieces.append(result_elem.text)
    for sub in result_elem.iter():
        if sub is result_elem:
            continue
        if sub.text and sub.text.strip():
            pieces.append(sub.text)
        if sub.tail and sub.tail.strip():
            pieces.append(sub.tail)
    return '\n'.join(pieces).strip()


def parse_jobs_all_from_text(text: str) -> List[Dict[str, str]]:
    jobs: List[Dict[str, str]] = []
    pattern = re.compile(
        r'^(?P<enq>\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})\s+'
        r'(?P<deq>(?:\d{2}:\d{2}:\d{2}|-))\s+'
        r'(?P<jobid>\d+)\s+'
        r'(?P<rest>.+?)\s*$'
    )
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith('Enqueued') or set(line) <= {'-'}:
            continue
        m = pattern.match(line)
        if not m:
            continue
        rest = m.group('rest')
        m2 = re.match(r'(?P<type>.+?)\s+(?P<status>FIN|ACT|PEND|FAIL)\s+(?P<result>OK|FAIL|PEND)\s+(?P<completed>.+)$', rest)
        if not m2:
            continue
        jobs.append({
            'job_id': m.group('jobid').strip(),
            'type': m2.group('type').strip(),
            'enqueued': m.group('enq').strip(),
            'dequeued': m.group('deq').strip(),
            'completed': m2.group('completed').strip(),
            'user': '',
        })
    return jobs


def get_jobs_all(host: str, api_key: str, verify_ssl: bool) -> List[Dict[str, str]]:
    root = api_op(host, api_key, SHOW_JOBS_ALL_CMD, verify_ssl)
    jobs = parse_jobs_all_from_xml(root)
    if jobs:
        return jobs
    text = extract_result_text(root)
    if text:
        return parse_jobs_all_from_text(text)
    return []


def parse_job_detail_xml(root: ET.Element) -> Dict[str, str]:
    job_nodes = recursive_find_job_nodes(root)
    if job_nodes:
        node = job_nodes[0]
        return {
            'job_id': xml_text(node.find('./id')) or recursive_find_first_text(root, ['id']),
            'type': xml_text(node.find('./type')) or recursive_find_first_text(root, ['type']),
            'user': xml_text(node.find('./user')) or recursive_find_first_text(root, ['user', 'username', 'admin']),
            'enqueued': xml_text(node.find('./tenq')) or xml_text(node.find('./enqueued')) or recursive_find_first_text(root, ['tenq', 'enqueued']),
            'dequeued': xml_text(node.find('./tdeq')) or xml_text(node.find('./dequeued')) or recursive_find_first_text(root, ['tdeq', 'dequeued']),
            'completed': xml_text(node.find('./tfin')) or xml_text(node.find('./completed')) or recursive_find_first_text(root, ['tfin', 'completed']),
        }
    return {
        'job_id': recursive_find_first_text(root, ['id']),
        'type': recursive_find_first_text(root, ['type']),
        'user': recursive_find_first_text(root, ['user', 'username', 'admin']),
        'enqueued': recursive_find_first_text(root, ['tenq', 'enqueued']),
        'dequeued': recursive_find_first_text(root, ['tdeq', 'dequeued']),
        'completed': recursive_find_first_text(root, ['tfin', 'completed']),
    }


def parse_job_detail_text(text: str, job_id: str) -> Dict[str, str]:
    rec = {'job_id': job_id, 'type': '', 'user': '', 'enqueued': '', 'dequeued': '', 'completed': ''}
    line_pat = re.compile(
        r'^(?P<enq>\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})\s+'
        r'(?P<deq>(?:\d{2}:\d{2}:\d{2}|-))\s+'
        r'(?P<jobid>\d+)\s+(?P<type>\S+)\s+\S+\s+\S+\s+(?P<completed>.+?)\s*$'
    )
    user_pat = re.compile(r'\b(?:User|user|Username|username)\b\s*[:=]\s*(?P<user>[^,\n]+)')
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = line_pat.match(line)
        if m and m.group('jobid') == str(job_id):
            rec['enqueued'] = m.group('enq').strip()
            rec['dequeued'] = m.group('deq').strip()
            rec['type'] = m.group('type').strip()
            rec['completed'] = m.group('completed').strip()
        um = user_pat.search(line)
        if um and not rec['user']:
            rec['user'] = um.group('user').strip()
    return rec


def get_job_detail(host: str, api_key: str, verify_ssl: bool, job_id: str) -> Dict[str, str]:
    root = api_op(host, api_key, SHOW_JOB_ID_CMD_TEMPLATE.format(job_id=job_id), verify_ssl)
    rec = parse_job_detail_xml(root)
    if rec.get('job_id') or rec.get('type') or rec.get('user'):
        return rec
    text = extract_result_text(root)
    if text:
        return parse_job_detail_text(text, job_id)
    return {'job_id': str(job_id), 'type': '', 'user': '', 'enqueued': '', 'dequeued': '', 'completed': ''}


# -------------------- time computation --------------------

def parse_enqueued(s: str) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ('%Y/%m/%d %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    return None


def parse_time_of_day(s: str, base_date: datetime) -> Optional[datetime]:
    if not s or s == '-' or s.endswith('%'):
        return None
    s = s.strip()
    try:
        t = datetime.strptime(s, '%H:%M:%S').time()
        return datetime.combine(base_date.date(), t)
    except ValueError:
        pass
    # 如果 completed 已经是完整 datetime
    for fmt in ('%Y/%m/%d %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def format_timedelta_seconds(seconds: int) -> str:
    if seconds < 0:
        return ''
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f'{h:02d}:{m:02d}:{s:02d}'
    return f'{m:02d}:{s:02d}'


def compute_durations(enqueued: str, dequeued: str, completed: str) -> Dict[str, str]:
    out = {'queue_time': '', 'execution_time': '', 'total_time': ''}
    enq_dt = parse_enqueued(enqueued)
    if enq_dt is None:
        return out
    deq_dt = parse_time_of_day(dequeued, enq_dt)
    fin_dt = parse_time_of_day(completed, enq_dt)

    if deq_dt is not None and deq_dt < enq_dt:
        # 极少数跨天场景保护
        from datetime import timedelta
        deq_dt = deq_dt + timedelta(days=1)
    if fin_dt is not None and fin_dt < enq_dt:
        from datetime import timedelta
        fin_dt = fin_dt + timedelta(days=1)

    if deq_dt is not None:
        out['queue_time'] = format_timedelta_seconds(int((deq_dt - enq_dt).total_seconds()))
    if deq_dt is not None and fin_dt is not None:
        out['execution_time'] = format_timedelta_seconds(int((fin_dt - deq_dt).total_seconds()))
    if fin_dt is not None:
        out['total_time'] = format_timedelta_seconds(int((fin_dt - enq_dt).total_seconds()))
    return out


# -------------------- file helpers --------------------

def load_existing_job_ids(output_file: Path) -> Set[str]:
    existing: Set[str] = set()
    if not output_file.exists():
        return existing
    for line in output_file.read_text(encoding='utf-8', errors='ignore').splitlines():
        if not line.strip():
            continue
        parts = line.split('\t')
        if parts and parts[0].strip() != 'job_id':
            existing.add(parts[0].strip())
    return existing


def append_records(output_file: Path, records: List[Dict[str, str]]) -> int:
    existing_ids = load_existing_job_ids(output_file)
    wrote = 0
    with output_file.open('a', encoding='utf-8') as f:
        if output_file.stat().st_size == 0:
            header = [
                f"{'job_id':<10}",
                f"{'user':<10}",
                f"{'enqueued':<20}",
                f"{'dequeued':<10}",
                f"{'completed':<20}",
                f"{'queue':<8}",
                f"{'exec':<8}",
                f"{'total':<8}",
            ]

            f.write("  ".join(header) + "\n")

        for rec in records:
            job_id = (rec.get('job_id') or '').strip()
            if not job_id or job_id in existing_ids:
                continue
            columns = [
                f"{job_id:<10}",
                f"{(rec.get('user') or '').strip():<10}",
                f"{(rec.get('enqueued') or '').strip():<20}",
                f"{(rec.get('dequeued') or '').strip():<10}",
                f"{(rec.get('completed') or '').strip():<20}",
                f"{(rec.get('queue_time') or '').strip():<8}",
                f"{(rec.get('execution_time') or '').strip():<8}",
                f"{(rec.get('total_time') or '').strip():<8}",
            ]

            line = "  ".join(columns)

            f.write(line + '\n')
            existing_ids.add(job_id)
            wrote += 1
    return wrote


# -------------------- main --------------------

def main() -> int:
    parser = argparse.ArgumentParser(description='从 Panorama 抓取 Commit to Panorama 任务，并追加写入 commit_time.txt（含执行时间）')
    parser.add_argument('--host', required=True, help='Panorama IP 或主机名')
    parser.add_argument('--username', help='管理员用户名（与 --password 一起用于 keygen）')
    parser.add_argument('--password', help='管理员密码（不提供时会提示输入）')
    parser.add_argument('--api-key', help='已生成的 API key；提供后将跳过 keygen')
    parser.add_argument('--output', default='commit_time.txt', help='输出文件名，默认 commit_time.txt')
    parser.add_argument('--verify-ssl', action='store_true', help='启用 SSL 证书校验（默认关闭）')
    args = parser.parse_args()

    if not args.api_key and not args.username:
        parser.error('必须提供 --api-key，或者提供 --username/--password 用于 keygen')

    if not args.api_key:
        password = args.password if args.password is not None else getpass.getpass('Panorama password: ')
        api_key = api_keygen(args.host, args.username, password, args.verify_ssl)
    else:
        api_key = args.api_key

    jobs = get_jobs_all(args.host, api_key, args.verify_ssl)
    if not jobs:
        print('没有获取到任何 job。')
        return 0

    commit_jobs = [j for j in jobs if (j.get('type') or '').strip() == 'Commit']
    if not commit_jobs:
        print('没有找到 Type=Commit 的 Panorama commit job。')
        return 0

    records: List[Dict[str, str]] = []
    for job in commit_jobs:
        job_id = (job.get('job_id') or '').strip()
        if not job_id:
            continue
        detail = get_job_detail(args.host, api_key, args.verify_ssl, job_id)
        record = {
            'job_id': job_id,
            'user': (detail.get('user') or '').strip(),
            'enqueued': (detail.get('enqueued') or job.get('enqueued') or '').strip(),
            'dequeued': (detail.get('dequeued') or job.get('dequeued') or '').strip(),
            'completed': (detail.get('completed') or job.get('completed') or '').strip(),
        }
        record.update(compute_durations(record['enqueued'], record['dequeued'], record['completed']))
        records.append(record)

    output_file = Path(args.output)
    if not output_file.exists():
        output_file.touch()
    wrote = append_records(output_file, records)
    print(f'共发现 {len(commit_jobs)} 条 Commit job，本次新增写入 {wrote} 条到 {output_file}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
