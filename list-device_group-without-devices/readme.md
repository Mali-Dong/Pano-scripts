# 📘 Panorama Cleanup Analyzer

## 📌 Overview

This script is designed for **Palo Alto Networks Panorama (PAN-OS 11.x)** environments to help Security Operations teams identify unused configuration objects and improve configuration hygiene.

The script connects to Panorama via the XML API and performs the following analysis:

- ✅ **Unused Device Groups**
- ✅ **Unused Template Stacks**
- ✅ **Templates not assigned to any Template Stack**
- ✅ Export results to a `.txt` report

---

## 🎯 Features

### 🔹 Device Group Analysis
- Identify **Device Groups with no assigned devices**
- Smart logic:
  - If a DG has **sub-DG**, it is considered **USED**
  - Only **leaf DGs** are evaluated for actual device usage

---

### 🔹 Template Stack Analysis
- Identify **Template Stacks without any attached devices**

---

### 🔹 Template Analysis
- Identify **Templates not referenced by any Template Stack**

---

### 🔹 Report Export
- Automatically generate a report file: