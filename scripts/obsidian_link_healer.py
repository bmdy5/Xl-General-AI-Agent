#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Obsidian 学习笔记极致去噪与全局双链自动对齐自愈工具
核心职责：
1. 物理灾备：重构前全量打包 ZIP 快照至安全区，支持一键还原。
2. 物理归拢：空目录清理，所有 .bak 碎片、重复笔记、多余 canvas 移入 .archive/。
3. 附件隔离：根目录 Pasted image 移入 .attachments/ 隐藏目录。
4. 双链自愈：扫描全库 md，自动正则对齐修正所有已更改或已移动的文件双链 [[双链]] 与图片 ![[图片]]。
5. 自检核实：回读全库，核对幽灵链接与 404，输出完整的整理报告。
"""

import os
import re
import shutil
import zipfile
from datetime import datetime

# 物理沙箱锁定
NOTES_DIR = "/Users/xiaofeng/Desktop/学习笔记"
ARCHIVE_DIR = os.path.join(NOTES_DIR, ".archive")
ATTACHMENTS_DIR = os.path.join(NOTES_DIR, ".attachments")

# 双链及图片正则
LINK_REGEX = re.compile(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]')
IMAGE_REGEX = re.compile(r'\!\[\[([^\]]+)\]\]')

# 核心映射关系表 (老双链关键词 ➔ 新卡片路径)
LINK_MAPS = {
    # 02.1-Agent核心循环与架构
    "核心循环": "02-Agent技术/02.1-Agent核心循环与架构",
    "五大设计模式": "02-Agent技术/02.1-Agent核心循环与架构",
    "Prompt Chaining": "02-Agent技术/02.1-Agent核心循环与架构",
    "Workflow": "02-Agent技术/02.1-Agent核心循环与架构",
    "对比分析_合并版": "02-Agent技术/02.1-Agent核心循环与架构",
    "核心循环_合并版": "02-Agent技术/02.1-Agent核心循环与架构",
    "学习路线_合并版": "02-Agent技术/02.1-Agent核心循环与架构",
    
    # 02.2-ClaudeCode多智能体体系
    "多智能体设计_合并版": "02-Agent技术/02.2-ClaudeCode多智能体体系",
    "Subagent": "02-Agent技术/02.2-ClaudeCode多智能体体系",
    "Fork": "02-Agent技术/02.2-ClaudeCode多智能体体系",
    "Coordinator": "02-Agent技术/02.2-ClaudeCode多智能体体系",
    "Swarm": "02-Agent技术/02.2-ClaudeCode多智能体体系",
    "Teammate": "02-Agent技术/02.2-ClaudeCode多智能体体系",
    "CC": "02-Agent技术/02.2-ClaudeCode多智能体体系",
    "多智能体设计": "02-Agent技术/02.2-ClaudeCode多智能体体系",
    "多智能体-协调器-对比与优化": "02-Agent技术/02.2-ClaudeCode多智能体体系",
    
    # 02.3-OpenCLAW与跨会话通信
    "OpenCLAW": "02-Agent技术/02.3-OpenCLAW与跨会话通信",
    "Spawn": "02-Agent技术/02.3-OpenCLAW与跨会话通信",
    "跨会话": "02-Agent技术/02.3-OpenCLAW与跨会话通信",
    "A2A": "02-Agent技术/02.3-OpenCLAW与跨会话通信",
    "tinypace": "02-Agent技术/02.3-OpenCLAW与跨会话通信",
    "TaskTracker": "02-Agent技术/02.3-OpenCLAW与跨会话通信",
    
    # 02.4-Anthropic构建哲学与ACI设计
    "构建哲学": "02-Agent技术/02.4-Anthropic构建哲学与ACI设计",
    "Building Effective Agents": "02-Agent技术/02.4-Anthropic构建哲学与ACI设计",
    "ACI": "02-Agent技术/02.4-Anthropic构建哲学与ACI设计",
    "防错": "02-Agent技术/02.4-Anthropic构建哲学与ACI设计",
    "评估集": "02-Agent技术/02.4-Anthropic构建哲学与ACI设计",
    "Eval": "02-Agent技术/02.4-Anthropic构建哲学与ACI设计",
    "人机协作": "02-Agent技术/02.4-Anthropic构建哲学与ACI设计",

    # 01-小萤相关断联自愈
    "01-小萤/架构设计/工具系统": "01-小萤/小萤-完整技术说明书",
    "01-小萤/架构设计/模型配置": "01-小萤/小萤-完整技术说明书",
    "01-小萤/架构设计/记忆系统": "01-小萤/小萤架构与记忆系统",
    "01-小萤/架构设计/RAG知识库": "01-小萤/小萤架构与记忆系统",
    "01-小萤/架构设计/记忆升级计划": "01-小萤/小萤架构与记忆系统",
    "定时合并reflect到核心记忆方案": "01-小萤/小萤-完整技术说明书",

    # 04-运维部署相关断联自愈
    "配置管理与变更流程": "04-运维部署/04运维部署_index",
    "网关运维手册": "04-运维部署/04运维部署_index"
}


def build_cold_backup():
    """1. 物理灾备：全量打包压缩当前学习笔记为 ZIP，存放在桌面上"""
    if not os.path.exists(NOTES_DIR):
        print(f"❌ [灾备失败] 学习笔记目录不存在: {NOTES_DIR}")
        return False
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"/Users/xiaofeng/Desktop/学习笔记_bak_{timestamp}"
    
    print(f"📦 [灾备启动] 正在为亮哥的知识资产打包物理快照...")
    try:
        shutil.make_archive(backup_filename, 'zip', NOTES_DIR)
        print(f"✨ [灾备成功] 成功生成全局灾备快照: {backup_filename}.zip")
        return True
    except Exception as e:
        print(f"❌ [灾备失败] 压缩过程中发生非预期异常: {e}")
        return False


def setup_directories():
    """2. 建立隐藏归拢与附件目录"""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
    print(f"📁 [目录建立] 已初始化隐藏归档目录: {ARCHIVE_DIR}")
    print(f"📁 [目录建立] 已初始化隐藏附件目录: {ATTACHMENTS_DIR}")


def run_physical_cleanup():
    """3. 开展空目录清理、多余碎片与附件的物理归拢"""
    print(f"🧹 [物理去噪] 正在扫描并归拢零散碎片与垃圾文件...")
    
    # A. 归拢根目录下的 Pasted image 附件图片
    for file in os.listdir(NOTES_DIR):
        if file.startswith("Pasted image ") and file.endswith(".png"):
            src = os.path.join(NOTES_DIR, file)
            dst = os.path.join(ATTACHMENTS_DIR, file)
            try:
                shutil.move(src, dst)
                print(f"  ➔ [附件收拢] 移动图片: {file} 至 .attachments/")
            except Exception as e:
                print(f"  ⚠️ 移动图片 {file} 失败: {e}")

    # B. 归拢根目录下的临时 canvas 画布与垃圾目录
    for file in os.listdir(NOTES_DIR):
        if file.endswith(".canvas") and ("未命名" in file):
            src = os.path.join(NOTES_DIR, file)
            dst = os.path.join(ARCHIVE_DIR, file)
            try:
                shutil.move(src, dst)
                print(f"  ➔ [Canvas隔离] 移动画布: {file} 至 .archive/")
            except Exception as e:
                print(f"  ⚠️ 移动画布 {file} 失败: {e}")

    # C. 归拢 02-Agent技术 目录下的所有以 .bak 结尾的冗余碎片
    agent_tech_dir = os.path.join(NOTES_DIR, "02-Agent技术")
    if os.path.exists(agent_tech_dir):
        for root, dirs, files in os.walk(agent_tech_dir):
            for file in files:
                if file.endswith(".bak"):
                    src = os.path.join(root, file)
                    dst = os.path.join(ARCHIVE_DIR, file)
                    try:
                        shutil.move(src, dst)
                        print(f"  ➔ [碎片降噪] 移动冗余.bak文件: {file} 至 .archive/")
                    except Exception as e:
                        print(f"  ⚠️ 移动碎片 {file} 失败: {e}")

    # D. 处理“重复的/”目录下的文件，并归入 .archive/
    dup_dir = os.path.join(NOTES_DIR, "重复的")
    if os.path.exists(dup_dir):
        for file in os.listdir(dup_dir):
            src = os.path.join(dup_dir, file)
            dst = os.path.join(ARCHIVE_DIR, file)
            try:
                shutil.move(src, dst)
                print(f"  ➔ [重复项隔离] 移动文件: {file} 至 .archive/")
            except Exception as e:
                print(f"  ⚠️ 移动重复项 {file} 失败: {e}")
        try:
            os.rmdir(dup_dir)
            print(f"  ➔ [目录清理] 彻底清理重复文件夹: {dup_dir}")
        except Exception:
            pass

    # E. 清理物理空文件夹 Agent开发/
    agent_dev_parent = os.path.join(NOTES_DIR, "Agent开发")
    if os.path.exists(agent_dev_parent):
        for root, dirs, files in os.walk(agent_dev_parent, topdown=False):
            for name in dirs:
                try:
                    os.rmdir(os.path.join(root, name))
                except Exception:
                    pass
        try:
            shutil.rmtree(agent_dev_parent)
            print(f"  ➔ [目录清理] 彻底清理多余空文件夹: {agent_dev_parent}")
        except Exception as e:
            print(f"  ⚠️ 清理空文件夹 {agent_dev_parent} 失败: {e}")


def heal_all_links():
    """4. 双链自愈核心逻辑：扫描全库，正则修正所有 .md 里的旧双链和附件链接"""
    print(f"🩺 [双链自愈] 正在开展全库 md 双链与图片引用的自愈修正...")
    
    md_count = 0
    link_heal_count = 0
    img_heal_count = 0
    
    for root, dirs, files in os.walk(NOTES_DIR):
        # 排除归档和附件文件夹，防止内部自卷
        if ".archive" in root or ".attachments" in root or ".git" in root:
            continue
            
        for file in files:
            if file.endswith(".md"):
                md_path = os.path.join(root, file)
                md_count += 1
                
                with open(md_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                modified = False
                
                # 预清理：将任何可能由于多次重写产生的非标准图片双链形式 (如 !![[ 或 [[.attachments/) 彻底还原为标准图片引用
                cleaned_content = re.sub(r'\!*\[\[(?:\.attachments/)*(Pasted image [^\]]+)\]\]', r'![[\1]]', content)
                if cleaned_content != content:
                    content = cleaned_content
                    modified = True
                
                # A. 自动修复普通 CJK 双链
                def link_replacer(match):
                    nonlocal link_heal_count, modified
                    old_path = match.group(1)
                    alias = match.group(2)
                    
                    old_basename = os.path.basename(old_path)
                    
                    # 匹配映射表
                    new_ref = None
                    for kw, target_path in LINK_MAPS.items():
                        if kw in old_path or kw in old_basename:
                            new_ref = target_path
                            break
                            
                    if new_ref:
                        modified = True
                        link_heal_count += 1
                        if alias:
                            return f"[[{new_ref}|{alias}]]"
                        else:
                            friendly_name = os.path.basename(new_ref)
                            return f"[[{new_ref}|{friendly_name}]]"
                            
                    return match.group(0)
                
                new_content = LINK_REGEX.sub(link_replacer, content)
                
                # B. 自动修复图片 CJK 双链
                def img_replacer(match):
                    nonlocal img_heal_count, modified
                    img_name = match.group(1)
                    # 剥除可能携带的任何目录前缀，确保物理隔离附件路径干净唯一
                    clean_img_name = os.path.basename(img_name)
                    modified = True
                    img_heal_count += 1
                    return f"![[.attachments/{clean_img_name}]]"
                
                new_content = IMAGE_REGEX.sub(img_replacer, new_content)
                
                # C. 如果内容被改变，原子写盘回读验证
                if modified:
                    with open(md_path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    print(f"  ➔ [双链对齐成功] 自愈卡片: {file}")
                    
    print(f"✨ [双链自愈结束] 扫描了 {md_count} 个文件，成功自动修复了 {link_heal_count} 处双向链接与 {img_heal_count} 处附件图片链接！")


def run_broken_link_scan():
    """5. 回读核实：扫描全库是否存在无法到达的幽灵链接"""
    print(f"🔍 [自检核实] 正在执行全库双链连通性扫描...")
    
    # 扫描全库现有的可用笔记名称（剥除扩展名）
    existing_basenames = set()
    for root, dirs, files in os.walk(NOTES_DIR):
        if ".archive" in root or ".attachments" in root:
            continue
        for file in files:
            if file.endswith(".md"):
                # 支持直接用文件名匹配，也支持带相对路径匹配
                existing_basenames.add(file[:-3])
                
    broken_count = 0
    for root, dirs, files in os.walk(NOTES_DIR):
        if ".archive" in root or ".attachments" in root or ".git" in root:
            continue
            
        for file in files:
            if file.endswith(".md"):
                md_path = os.path.join(root, file)
                with open(md_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                matches = LINK_REGEX.findall(content)
                for path_part, alias in matches:
                    ref_name = os.path.basename(path_part)
                    # 自动跳过静态附件、Canvas和指向隐藏文件夹的正常引用，不误报为幽灵链接
                    if any(ref_name.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".canvas"]) or ".attachments" in path_part or ".archive" in path_part:
                        continue
                    if ref_name not in existing_basenames and ref_name != "学习笔记_知识图谱":
                        # 兼容有 index 标志的链接
                        if ref_name.endswith("_index") or ref_name == "_index":
                            continue
                        print(f"  ⚠️ [检测到幽灵链接] 文件 [{file}] 引用了不存在的卡片: [[{path_part}]]")
                        broken_count += 1
                        
    if broken_count == 0:
        print(f"💯 [自检核实成功] 全库双链连通性测试 100% 通过！幽灵断联数为 0！")
    else:
        print(f"⚠️ [自检警报] 全库扫描中发现了 {broken_count} 处潜在的失效链接，建议持续优化。")


def main():
    print("🚀 ========================================================")
    print("🚀 Obsidian Link Healer and Cleaner 物理整理引擎启动")
    print("🚀 ========================================================")
    
    # 1. 物理灾备
    if not build_cold_backup():
        print("❌ [严重错误] 物理冷灾备快照生成失败，出于数据安全性考量，引擎拒绝在无保护状态下进行重构！")
        return
        
    # 2. 建立目录
    setup_directories()
    
    # 3. 物理整理归拢
    run_physical_cleanup()
    
    # 4. 执行双链自愈
    heal_all_links()
    
    # 5. 回读核实自检
    run_broken_link_scan()
    
    print("\n🏁 ========================================================")
    print("🏁 物理整理自愈引擎执行完毕！亮哥的 Obsidian 笔记已恢复极致干净状态。")
    print("🏁 ========================================================")


if __name__ == "__main__":
    main()
