#!/usr/bin/env python3
"""
从 Panorama 获取“Commit to Panorama”任务，并将 Job ID、用户名、Enqueued、Dequeued、Completed
以追加方式写入 commit_time.txt；如果 commit_time.txt 中已存在相同 Job ID，则跳过。

实现说明：
- 官方明确支持：
  1) 使用用户名/密码生成 API key。
  2) 使用 XML API 的 type=op 执行操作模式命令。\n
- 下面脚本采用 XML API 调用 `show jobs all` 和 `show jobs id <jobid>`，
  并同时支持“结构化 XML 返回”和“文本表格返回”两种解析方式。
- 如果你的设备返回格式与这里略有差异，通常只需微调正则或 XML 标签提取部分。

用法示例：
python panorama_commit_jobs.py \
  --host 10.0.0.10 \
  --username admin \
  --password 'YourPassword' \
  --output commit_time.txt

也支持直接传 API key：
python panorama_commit_jobs.py --host 10.0.0.10 --api-key <API_KEY>
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
import xml.etree.ElementTree as ET

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

API_TIMEOUT = 30
# 下面两个 cmd XML 是按照 CLI 结构映射的实现写法；如果你的设备使用不同 XML，可在这里改。
SHOW_JOBS_ALL_CMD = '<show><jobs><all></all></jobs></show>'
SHOW_JOB_ID_CMD_TEMPLATE = '<show><jobs><id>{job_id}</id></jobs></show>'


def api_keygen(host: str, username: str, password: str, verify_ssl: bool) -> str:
    url = f'https://{host}/api/'
    params = {
        'type': 'keygen',
        'user': username,
        'password': password,
    }
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


def xml_text(elem: Optional[ET.Element]) -> str:
    return '' if elem is None or elem.text is None else elem.text.strip()


def recursive_find_first_text(root: ET.Element, tag_names: Iterable[str]) -> str:
    tag_set = {t.lower() for t in tag_names}
    for elem in root.iter():
        if elem.tag.lower() in tag_set and (elem.text or '').strip():
            return elem.text.strip()
    return ''


def recursive_find_job_nodes(root: ET.Element) -> List[ET.Element]:
    """尽量找出 XML 中代表 job 的节点。"""
    # 常见情况：<job>...</job> 或 <entry><id>...</id>...</entry>
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
    # 有些 op 返回把 CLI 表格放在 <result> 内部文本中
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
    text = '\n'.join(pieces).strip()
    return text


def parse_jobs_all_from_text(text: str) -> List[Dict[str, str]]:
    jobs: List[Dict[str, str]] = []
    # 只取看起来像 show jobs all 表格数据的行
    # 格式大致：
    # 2026/06/05 05:31:40   05:31:40      6070168  Commit  FIN OK 05:40:23
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
        # 从末尾倒推：Status, Result, Completed
        # 例：Commit                            FIN     OK 05:40:23
        # 例：CommitAll                         FIN     OK 100 %
        m2 = re.match(r'(?P<type>.+?)\s+(?P<status>FIN|ACT|PEND|FAIL)\s+(?P<result>OK|FAIL|PEND)\s+(?P<completed>.+)$', rest)
        if not m2:
            continue
        rec = {
            'job_id': m.group('jobid'),
            'type': m2.group('type').strip(),
            'enqueued': m.group('enq').strip(),
            'dequeued': m.group('deq').strip(),
            'completed': m2.group('completed').strip(),
            'user': '',
        }
        jobs.append(rec)
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
    # 尝试从 XML 结构化字段提取
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
    # 没找到 job 节点，则尽量从任意标签中拿
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
    # 解析顶部表格行
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


def load_existing_job_ids(output_file: Path) -> Set[str]:
    existing: Set[str] = set()
    if not output_file.exists():
        return existing
    for line in output_file.read_text(encoding='utf-8', errors='ignore').splitlines():
        if not line.strip():
            continue
        # 文件格式：job_id\tuser\tenqueued\tdequeued\tcompleted
        parts = line.split('\t')
        if parts:
            existing.add(parts[0].strip())
    return existing


def append_records(output_file: Path, records: List[Dict[str, str]]) -> int:
    existing_ids = load_existing_job_ids(output_file)
    wrote = 0
    with output_file.open('a', encoding='utf-8') as f:
        if output_file.stat().st_size == 0:
            f.write('job_id\tuser\tenqueued\tdequeued\tcompleted\n')
        for rec in records:
            job_id = (rec.get('job_id') or '').strip()
            if not job_id or job_id in existing_ids:
                continue
            line = '\t'.join([
                job_id,
                (rec.get('user') or '').strip(),
                (rec.get('enqueued') or '').strip(),
                (rec.get('dequeued') or '').strip(),
                (rec.get('completed') or '').strip(),
            ])
            f.write(line + '\n')
            existing_ids.add(job_id)
            wrote += 1
    return wrote


def main() -> int:
    parser = argparse.ArgumentParser(description='从 Panorama 抓取 Commit to Panorama 任务并追加写入 commit_time.txt')
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

    # 只关注 Commit to Panorama：Type == Commit
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
        # detail 优先；为空时回退到 all 列表中的字段。
        record = {
            'job_id': job_id,
            'user': (detail.get('user') or '').strip(),
            'enqueued': (detail.get('enqueued') or job.get('enqueued') or '').strip(),
            'dequeued': (detail.get('dequeued') or job.get('dequeued') or '').strip(),
            'completed': (detail.get('completed') or job.get('completed') or '').strip(),
        }
        records.append(record)

    output_file = Path(args.output)
    if not output_file.exists():
        output_file.touch()
    wrote = append_records(output_file, records)
    print(f'共发现 {len(commit_jobs)} 条 Commit job，本次新增写入 {wrote} 条到 {output_file}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
