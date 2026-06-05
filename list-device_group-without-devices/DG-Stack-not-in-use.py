#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import requests
import urllib3
import xml.etree.ElementTree as ET
from getpass import getpass
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# =========================
# 输入与认证
# =========================

def prompt_input():
    host = input("Panorama IP/FQDN: ").strip()
    if not host:
        print("Panorama IP/FQDN 不能为空")
        sys.exit(1)

    port = input("Port [443]: ").strip() or "443"

    username = input("Username: ").strip()
    if not username:
        print("用户名不能为空")
        sys.exit(1)

    password = getpass("Password: ")
    if not password:
        print("密码不能为空")
        sys.exit(1)

    verify_ssl_in = input("Verify SSL certificate? [y/N]: ").strip().lower()
    verify_ssl = verify_ssl_in in ("y", "yes")

    action = input("Config action [show/get] (default: show): ").strip().lower() or "show"
    if action not in ("show", "get"):
        print("Config action 只能是 show 或 get")
        sys.exit(1)

    return host, port, username, password, verify_ssl, action


def get_api_key(base_url, username, password, verify_ssl):
    params = {
        "type": "keygen",
        "user": username,
        "password": password
    }

    resp = requests.get(
        f"{base_url}/api/",
        params=params,
        verify=verify_ssl,
        timeout=30
    )
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    if root.attrib.get("status") != "success":
        msg = root.findtext(".//msg")
        raise RuntimeError(f"API key 生成失败: {msg or resp.text}")

    key = root.findtext(".//key")
    if not key:
        raise RuntimeError("未在返回中找到 API key")

    return key


def write_result_to_file(unused_dg, unused_stacks, orphan_templates):
    """
    输出结果到 txt 文件
    文件名格式：yyyy-mm-dd-dg-stack-not-in-use.txt
    """

    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"{today}-dg-stack-not-in-use.txt"

    with open(filename, "w", encoding="utf-8") as f:

        # ========= DG =========
        f.write("===== UNUSED DEVICE GROUP =====\n")
        f.write(f"总数: {len(unused_dg)}\n")
        for dg in unused_dg:
            f.write(f"{dg}\n")

        f.write("\n")

        # ========= Template Stack =========
        f.write("===== UNUSED TEMPLATE STACK =====\n")
        f.write(f"总数: {len(unused_stacks)}\n")
        for stack in unused_stacks:
            f.write(f"{stack}\n")

        f.write("\n")

        # ========= Template =========
        f.write("===== TEMPLATE NOT IN ANY STACK =====\n")
        f.write(f"总数: {len(orphan_templates)}\n")
        for tpl in orphan_templates:
            f.write(f"{tpl}\n")

    print(f"\n✅ 结果已写入文件: {filename}")


# =========================
# XML 获取通用函数
# =========================

def get_config_xml(base_url, api_key, verify_ssl, xpath, action="show"):
    params = {
        "type": "config",
        "action": action,
        "xpath": xpath,
        "key": api_key
    }

    resp = requests.get(
        f"{base_url}/api/",
        params=params,
        verify=verify_ssl,
        timeout=60
    )
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    if root.attrib.get("status") != "success":
        msg = root.findtext(".//msg")
        raise RuntimeError(f"获取配置失败: {msg or resp.text}")

    return root


def get_device_groups_xml(base_url, api_key, verify_ssl, action="show"):
    xpath = "/config/devices/entry[@name='localhost.localdomain']/device-group"
    return get_config_xml(base_url, api_key, verify_ssl, xpath, action)


def get_template_stacks_xml(base_url, api_key, verify_ssl, action="show"):
    xpath = "/config/devices/entry[@name='localhost.localdomain']/template-stack"
    return get_config_xml(base_url, api_key, verify_ssl, xpath, action)


def get_templates_xml(base_url, api_key, verify_ssl, action="show"):
    xpath = "/config/devices/entry[@name='localhost.localdomain']/template"
    return get_config_xml(base_url, api_key, verify_ssl, xpath, action)


# =========================
# DG 分析
# =========================

def parse_unused_device_groups(xml_root):
    """
    规则：
    1. 如果 DG 有 sub-DG，则认为此 DG 是 USED
    2. 只有叶子 DG 才判断设备数量
    3. 如果叶子 DG 没有设备，则认为 UNUSED

    为了提高兼容性：
    - 先尝试通过 parent-dg 构建父子关系
    - 再用名称前缀方式补充判断（例如 AZR -> AZR Internal Production）
    """

    dg_entries = xml_root.findall(".//result/device-group/entry")
    if not dg_entries:
        dg_entries = xml_root.findall(".//result/entry")

    dg_devices = {}
    dg_has_children = {}
    dg_parent = {}

    # 1. 收集设备数
    for dg in dg_entries:
        name = dg.attrib.get("name")
        if not name:
            continue

        device_entries = dg.findall("./devices/entry")
        device_members = dg.findall("./devices/member")
        device_count = len(device_entries) + len(device_members)

        dg_devices[name] = device_count
        dg_has_children[name] = False

    all_groups = sorted(dg_devices.keys())

    # 2. 先尝试根据 parent-dg 构建 has_children
    for dg in dg_entries:
        name = dg.attrib.get("name")
        if not name:
            continue

        parent = dg.findtext("./parent-dg")
        if parent:
            dg_parent[name] = parent

    for child, parent in dg_parent.items():
        if parent in dg_has_children:
            dg_has_children[parent] = True

    # 3. 再通过名称前缀补充判断（适配你的 AZR 场景）
    #    支持空格、连字符、下划线、斜杠这几种常见命名习惯
    separators = [" ", "-", "_", "/"]

    for dg in all_groups:
        for other in all_groups:
            if other == dg:
                continue

            # 已经通过 parent-dg 判定为有下级，就不用再看
            if dg_has_children[dg]:
                break

            for sep in separators:
                if other.startswith(dg + sep):
                    dg_has_children[dg] = True
                    break

    # 4. Debug 结构输出
    print("\n=== DG STRUCTURE DEBUG ===")
    for dg in all_groups:
        print(f"[STRUCT] DG: {dg} | has_children={dg_has_children[dg]} | devices={dg_devices[dg]}")
    print("==========================\n")

    # 5. 判断 unused
    unused = []

    for dg in all_groups:
        print(f"[CHECK] DG: {dg}")

        # 规则1：有子DG则直接视为 USED
        if dg_has_children.get(dg):
            print(f"[RESULT] DG: {dg} -> HAS sub-DG → USED")
            continue

        # 规则2：叶子DG判断设备数
        device_count = dg_devices.get(dg, 0)

        if device_count == 0:
            print(f"[RESULT] DG: {dg} -> NO device → UNUSED")
            unused.append(dg)
        else:
            print(f"[RESULT] DG: {dg} -> device_count={device_count} → USED")

    return all_groups, sorted(unused)


# =========================
# Template Stack 分析
# =========================

def parse_unused_template_stacks(xml_root):
    """
    规则：
    - template-stack 下没有任何设备 -> UNUSED
    """

    stack_entries = xml_root.findall(".//result/template-stack/entry")
    if not stack_entries:
        stack_entries = xml_root.findall(".//result/entry")

    all_stacks = []
    unused_stacks = []

    print("\n=== TEMPLATE STACK DEBUG ===")

    for stack in stack_entries:
        name = stack.attrib.get("name", "<unnamed>")
        all_stacks.append(name)

        # 设备解析（兼容两种常见结构）
        device_entries = stack.findall("./devices/entry")
        device_members = stack.findall("./devices/member")
        device_count = len(device_entries) + len(device_members)

        # 也把 stack 引用的 templates 打出来，便于 debug
        templates = [m.text for m in stack.findall("./templates/member") if m.text]
        if not templates:
            templates = [
                m.attrib.get("name")
                for m in stack.findall("./templates/entry")
                if m.attrib.get("name")
            ]

        print(f"[CHECK] Template-Stack: {name}")
        print(f"        devices={device_count}, templates={templates}")

        if device_count == 0:
            print(f"[RESULT] Template-Stack: {name} -> NO device → UNUSED")
            unused_stacks.append(name)
        else:
            print(f"[RESULT] Template-Stack: {name} -> device_count={device_count} → USED")

    print("=================================\n")

    return sorted(all_stacks), sorted(unused_stacks)


# =========================
# Template 分析
# =========================

def parse_templates_not_in_any_stack(templates_xml, stacks_xml):
    """
    规则：
    - template 没有被任何 template-stack 引用 -> NOT in any stack
    """

    # 所有 templates
    template_entries = templates_xml.findall(".//result/template/entry")
    if not template_entries:
        template_entries = templates_xml.findall(".//result/entry")

    all_templates = sorted({
        t.attrib.get("name")
        for t in template_entries
        if t.attrib.get("name")
    })

    # 所有 stack 中被引用的 templates
    stack_entries = stacks_xml.findall(".//result/template-stack/entry")
    if not stack_entries:
        stack_entries = stacks_xml.findall(".//result/entry")

    templates_in_stack = set()

    print("\n=== TEMPLATE MEMBERSHIP DEBUG ===")

    for stack in stack_entries:
        stack_name = stack.attrib.get("name", "<unnamed>")

        members = [m.text for m in stack.findall("./templates/member") if m.text]

        if not members:
            members = [
                m.attrib.get("name")
                for m in stack.findall("./templates/entry")
                if m.attrib.get("name")
            ]

        print(f"[STACK] {stack_name} -> templates={members}")

        for t in members:
            templates_in_stack.add(t)

    not_in_stack = sorted([
        t for t in all_templates
        if t not in templates_in_stack
    ])

    for t in all_templates:
        print(f"[CHECK] Template: {t}")
        if t in templates_in_stack:
            print(f"[RESULT] Template: {t} -> IN template-stack")
        else:
            print(f"[RESULT] Template: {t} -> NOT in any stack → UNUSED")

    print("=================================\n")

    return all_templates, sorted(templates_in_stack), not_in_stack


# =========================
# main
# =========================

def main():
    host, port, username, password, verify_ssl, action = prompt_input()
    base_url = f"https://{host}:{port}"

    try:
        print("\n[1/6] 正在生成 API key ...")
        api_key = get_api_key(base_url, username, password, verify_ssl)

        # -------------------------------
        # DG
        # -------------------------------
        print("[2/6] 正在获取 Device Group 配置 ...")
        dg_xml = get_device_groups_xml(base_url, api_key, verify_ssl, action=action)

        print("[3/6] 正在分析未被任何设备使用的 Device Group ...")
        all_groups, unused_groups = parse_unused_device_groups(dg_xml)

        print("\n========== DEVICE GROUP RESULT ==========")
        print(f"Device Group 总数: {len(all_groups)}")

        if not all_groups:
            print("未获取到任何 Device Group。")
            print("请检查：")
            print("1) 账号权限是否足够")
            print("2) action=show / get 是否适合你的环境")
            print("3) Panorama 返回的 XPath 内容是否与你环境一致")
        else:
            if unused_groups:
                print(f"未被任何设备使用的 Device Group 数量: {len(unused_groups)}")
                for name in unused_groups:
                    print(f" - {name}")
            else:
                print("未发现没有设备成员的 Device Group。")
        print("=========================================\n")

        # -------------------------------
        # Template Stack
        # -------------------------------
        print("[4/6] 正在获取 Template Stack 配置 ...")
        template_stacks_xml = get_template_stacks_xml(
            base_url, api_key, verify_ssl, action=action
        )

        print("[5/6] 正在获取 Template 配置 ...")
        templates_xml = get_templates_xml(
            base_url, api_key, verify_ssl, action=action
        )

        print("[6/6] 正在分析 Template Stack / Template 使用情况 ...")
        all_stacks, unused_stacks = parse_unused_template_stacks(template_stacks_xml)

        all_templates, used_templates, orphan_templates = parse_templates_not_in_any_stack(
            templates_xml,
            template_stacks_xml
        )

        write_result_to_file(
            unused_groups,
            unused_stacks,
            orphan_templates
    )

        print("\n========== TEMPLATE STACK RESULT ==========")
        print(f"Template Stack 总数: {len(all_stacks)}")

        if unused_stacks:
            print(f"未使用的 Template Stack 数量: {len(unused_stacks)}")
            for s in unused_stacks:
                print(f" - {s}")
        else:
            print("未发现未使用的 Template Stack")
        print("==========================================\n")

        print("========== TEMPLATE RESULT ==========")
        print(f"Template 总数: {len(all_templates)}")

        if orphan_templates:
            print(f"未被任何 Template Stack 使用的 Template 数量: {len(orphan_templates)}")
            for t in orphan_templates:
                print(f" - {t}")
        else:
            print("所有 Template 均已被 Template Stack 使用")
        print("====================================\n")

    except requests.exceptions.RequestException as e:
        print(f"HTTP/连接错误: {e}")
        sys.exit(3)
    except ET.ParseError as e:
        print(f"XML 解析失败: {e}")
        sys.exit(4)
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(5)


if __name__ == "__main__":
    main()