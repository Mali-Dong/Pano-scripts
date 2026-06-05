# 📘 Panorama Cleanup Analyzer

## 📌 Overview

This script is designed for **Palo Alto Networks Panorama (PAN-OS 11.x)** environments to help Security Operations teams identify unused configuration objects and improve configuration hygiene.

The script connects to Panorama via the XML API and performs the following analysis:

* ✅ **Unused Device Groups (DG)**
* ✅ **Unused Template Stacks**
* ✅ **Templates not assigned to any Template Stack**
* ✅ Export results to a `.txt` report

\---

## 🎯 Features

### 🔹 Device Group Analysis

* Identify **Device Groups with no assigned devices**
* Smart logic:

  * If a DG has **sub-DG**, it is considered **USED**
  * Only **leaf DGs** are evaluated for device binding

\---

### 🔹 Template Stack Analysis

* Identify **Template Stacks without any attached devices**

\---

### 🔹 Template Analysis

* Identify **Templates not referenced by any Template Stack**

\---

### 🔹 Report Export

Automatically generates:

```
yyyy-mm-dd-dg-stack-not-in-use.txt
```

Example:

```
2026-06-04-dg-stack-not-in-use.txt
```

\---

## 🛠️ Requirements

* Python 3.x
* Required library:

```bash
pip install requests
```

\---

## 🚀 Usage

Run the script:

```bash
python DG-without-devices.py
```

\---

## 🧩 Interactive Input

When running the script, you will be prompted for the following parameters:

|Input|Description|
|-|-|
|Panorama IP/FQDN|Panorama management address|
|Port|Default: 443|
|Username|Panorama login username|
|Password|Secure input|
|SSL verification|y / n|
|Config action|`show` (default) or `get`|

\---

## 📊 Console Output Example

### ✅ Device Group

```
\[CHECK] DG: AZR
\[RESULT] DG: AZR -> HAS sub-DG → USED

\[CHECK] DG: Legacy-DG
\[RESULT] DG: Legacy-DG -> NO device → UNUSED
```

\---

### ✅ Template Stack

```
\[CHECK] Template-Stack: prod-stack
        devices=3, templates=\['tpl-core', 'tpl-dmz']
\[RESULT] Template-Stack: prod-stack -> USED

\[CHECK] Template-Stack: old-stack
        devices=0
\[RESULT] Template-Stack: old-stack -> UNUSED
```

\---

### ✅ Template

```
\[STACK] prod-stack -> templates=\['tpl-core', 'tpl-dmz']

\[CHECK] Template: tpl-old
\[RESULT] Template: tpl-old -> NOT in any stack → UNUSED
```

\---

## 📄 Output File Example

Generated file:

```
2026-06-04-dg-stack-not-in-use.txt
```

\---

## ⚙️ Technical Notes

* Uses PAN-OS XML API with XPath queries
* Compatible with Panorama 11.x
* Supports multiple XML structures for templates and template stacks

\---

## 📌 Summary

|Feature|Status|
|-|-|
|Unused Device Group|✅|
|Unused Template Stack|✅|
|Orphan Template|✅|
|TXT Report Export|✅|

\---

✅ This script provides **high-value Panorama cleanup automation for SecOps teams**.

