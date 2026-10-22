#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同步全局提示词：把《常驻通用规则.md》的提示词正文分发到各 AI 工具的全局提示词位置。

用法:
  python 同步全局提示词.py               检查模式（默认，只读，报告各目标是否过期）
  python 同步全局提示词.py apply         应用模式（先备份再写入，跳过已一致的目标）
  python 同步全局提示词.py apply codex   只应用指定目标（可跟多个名称）

目标类型（见 同步配置.json）:
  markdown        整个文件 = 包装模板 + 正文；无包装模板时为纯正文
  markdown-static 整个文件 = 包装模板（指针式引用源文件，不随源正文变化）
  json-field      JSON 文件中按字段路径定位的字符串字段 = 包装模板 + 正文
  ccgui-prompt    ccgui prompt.json 中按名称定位的提示词条目，content = 包装模板 + 正文

安全设计:
  - apply 写入前自动备份为 <原文件名>.bak-同步-<时间戳>
  - 写入后回读校验，不一致按失败报告且不删备份
  - 内容比较忽略 CRLF/LF 换行差异
  - json-field / ccgui-prompt 目标为整文件重写：写回前探测原文件缩进风格并沿用；
    未识别（非标准 json.dumps 风格）时按默认 2 空格缩进重写并在输出中注明
  - 建议先退出对应工具（尤其 paseo/ccgui）再 apply，避免运行中的程序回写覆盖

退出码: 0=全部一致或全部更新成功；1=存在差异或有目标失败（含目标文件损坏，逐目标报告、不中断剩余目标）；2=运行错误（脚本自身或配置错误）
"""
import json
import os
import shutil
import sys
import time
import uuid

配置目录 = os.path.dirname(os.path.abspath(__file__))
仓库根目录 = os.path.dirname(配置目录)


def 解析路径(路径):
    """展开 ~ 为当前用户主目录；相对路径按仓库根目录定位，保证不同用户名、不同克隆位置可用。"""
    路径 = os.path.expanduser(路径)
    if not os.path.isabs(路径):
        路径 = os.path.normpath(os.path.join(仓库根目录, 路径))
    return 路径


class 体系错误(Exception):
    """脚本自身或配置导致的错误，与目标内容差异无关。"""


def 归一(文本):
    """统一换行符后比较，避免 CRLF/LF 差异误报。"""
    return 文本.replace('\r\n', '\n')


def 读取源正文(源文件):
    """提取源文件 ## 提示词正文 下 ```text 围栏内的正文；围栏缺失或正文过短立即报错，防止分发错误内容。"""
    if not os.path.isfile(源文件):
        raise 体系错误('源文件不存在: %s' % 源文件)
    行 = open(源文件, encoding='utf-8').read().split('\n')
    起 = next((i for i, l in enumerate(行) if l.strip() == '```text'), None)
    if 起 is None:
        raise 体系错误('源文件缺少 ```text 围栏')
    止 = next((i for i, l in enumerate(行) if i > 起 and l.strip() == '```'), None)
    if 止 is None:
        raise 体系错误('源文件围栏未闭合')
    正文 = '\n'.join(l.rstrip('\r') for l in 行[起 + 1:止]).strip('\n')
    if len(正文) < 200:
        raise 体系错误('源正文过短(%d 字符)，疑似解析错误，终止分发' % len(正文))
    return 正文


def 读取包装(文件名):
    """读取包装模板；模板尾部空行原样保留（zcode 模板依赖尾部空行分隔标题与正文）。"""
    if not 文件名:
        return ''
    路径 = os.path.join(配置目录, 文件名)
    if not os.path.isfile(路径):
        raise 体系错误('包装模板不存在: %s' % 文件名)
    return 归一(open(路径, encoding='utf-8').read())


def 期望内容(目标, 正文):
    """按目标类型拼出期望的提示词内容（统一 LF 结尾，写入时再按目标习惯转 CRLF）。"""
    包装 = 读取包装(目标.get('包装文件'))
    if 目标['类型'] == 'markdown-static':
        return 包装.rstrip('\n') + '\n'
    if not 包装:
        return 正文 + '\n'
    return 包装 + 正文 + '\n'


def 读取JSON(文件):
    with open(文件, encoding='utf-8') as f:
        return json.load(f)


def 取字段(数据, 字段路径):
    for 键 in 字段路径:
        数据 = 数据[键]
    return 数据


def 置字段(数据, 字段路径, 值):
    """沿字段路径写入值；中间键缺失时逐层补建空对象，已存在但不是对象时报错不覆盖。"""
    父 = 数据
    for 键 in 字段路径[:-1]:
        if 键 not in 父:
            父[键] = {}
        父 = 父[键]
        if not isinstance(父, dict):
            raise 体系错误('字段路径「%s」的中间键「%s」已存在且不是对象，疑似结构异常，不覆盖'
                           % ('.'.join(字段路径), 键))
    父[字段路径[-1]] = 值


def 定位ccgui条目(数据, 名称, 允许缺失=False):
    """按名称在 prompts 中定位唯一条目；重名视为配置错误；允许缺失时不报错返回 None（apply 时新建）。

    prompts 键缺失或不是对象时视为目标文件结构异常，直接报失败而不静默重建，避免覆盖外部数据。
    """
    prompts = 数据.get('prompts') if isinstance(数据, dict) else None
    if not isinstance(prompts, dict):
        raise 体系错误('ccgui 目标文件缺少可用的 prompts 对象，疑似结构异常，不自动重建')
    匹配 = [p for p in prompts.values() if isinstance(p, dict) and p.get('name') == 名称]
    if len(匹配) == 1:
        return 匹配[0]
    if not 匹配 and 允许缺失:
        return None
    raise 体系错误('ccgui 提示词「%s」匹配 %d 条，应为 1 条' % (名称, len(匹配)))


def 读取目标当前值(目标):
    """返回 (当前值或 None, 已载入的JSON数据或None)；文件缺失返回 None 而不是报错。

    目标文件损坏（编码错误、JSON 解析失败、权限不足等）转译为体系错误，由主循环按单目标
    失败报告并继续剩余目标；ValueError 涵盖 JSONDecodeError 与 UnicodeDecodeError。
    """
    文件 = 目标['文件']
    if not os.path.isfile(文件):
        return None, None
    try:
        if 目标['类型'] in ('markdown', 'markdown-static'):
            return open(文件, encoding='utf-8').read(), None
        数据 = 读取JSON(文件)
        if 目标['类型'] == 'json-field':
            try:
                return 取字段(数据, 目标['字段路径']), 数据
            except (KeyError, TypeError):
                return None, 数据
        if 目标['类型'] == 'ccgui-prompt':
            条目 = 定位ccgui条目(数据, 目标['提示词名称'], 允许缺失=True)
            return (条目 or {}).get('content'), 数据
        raise 体系错误('未知目标类型: %s' % 目标['类型'])
    except 体系错误:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as e:
        raise 体系错误('目标文件无法读取: %s（%s: %s）' % (文件, type(e).__name__, e))


def 差异摘要(当前值, 期望):
    """给出第一处不同行的行号和片段，便于人工判断差异是否合理。"""
    当前行 = 归一(当前值).split('\n')
    期望行 = 期望.split('\n')
    for i, (a, b) in enumerate(zip(当前行, 期望行), 1):
        if a != b:
            return '第 %d 行起不同：现为 %r，应为 %r' % (i, a[:30], b[:30])
    return '行数不同：现 %d 行，应为 %d 行' % (len(当前行), len(期望行))


def 探测JSON缩进(数据, 原文本):
    """探测原文件与 json.dumps 兼容的缩进风格（隐含键序与分隔符一致）；识别不了返回 None。

    必须在修改 数据 前调用：用未修改的解析结果反序列化比对原文本，整文件重写时沿用该风格，
    避免单字段同步重排 paseo/ccgui 等外部程序管理文件的格式。
    """
    if not 原文本:
        return None
    基准 = 归一(原文本).rstrip('\n')
    for 缩进 in list(range(1, 9)) + ['\t']:
        if 归一(json.dumps(数据, ensure_ascii=False, indent=缩进)).rstrip('\n') == 基准:
            return 缩进
    return None


def 目标换行符(文件, 文本):
    """沿用目标文件现有换行风格；新文件默认 CRLF（Windows 习惯）。"""
    if os.path.isfile(文件):
        return '\r\n' if b'\r\n' in open(文件, 'rb').read() else '\n'
    return '\r\n'


def 写入文件(文件, 文本, 换行):
    """按指定换行风格写入并保证恰好一个结尾换行。"""
    内容 = 归一(文本)
    if not 内容.endswith('\n'):
        内容 += '\n'
    with open(文件, 'w', encoding='utf-8', newline='') as f:
        f.write(内容.replace('\n', 换行))


def 应用目标(目标, 期望, 数据):
    """备份后写入单个目标并回读校验；返回 (是否成功, 说明)。

    JSON 类目标为整文件重写：必须在修改 数据 前探测原文本缩进风格，写回时沿用；
    中间键缺失由 置字段 逐层补建；回读校验也在异常保护内（读取目标当前值 可能抛体系错误）。
    """
    文件 = 目标['文件']
    目录 = os.path.dirname(文件)
    if 目录 and not os.path.isdir(目录):
        return False, '目标目录不存在'
    备份 = 文件 + '.bak-同步-' + time.strftime('%Y%m%d-%H%M%S')
    原文本 = None
    缩进 = None
    if os.path.isfile(文件):
        shutil.copy2(文件, 备份)
    try:
        if 目标['类型'] in ('markdown', 'markdown-static'):
            写入文件(文件, 期望, 目标换行符(文件, 期望))
        else:
            原文本 = open(文件, encoding='utf-8').read() if os.path.isfile(文件) else None
            缩进 = 探测JSON缩进(数据, 原文本)
            if 数据 is None:
                数据 = {'prompts': {}} if 目标['类型'] == 'ccgui-prompt' else {}
            if 目标['类型'] == 'json-field':
                置字段(数据, 目标['字段路径'], 期望.rstrip('\n'))
            else:
                条目 = 定位ccgui条目(数据, 目标['提示词名称'], 允许缺失=True)
                if 条目 is None:
                    新id = str(uuid.uuid4())
                    条目 = {'id': 新id, 'name': 目标['提示词名称'], 'content': '',
                            'createdAt': int(time.time() * 1000), 'updatedAt': int(time.time() * 1000)}
                    数据['prompts'][新id] = 条目
                条目['content'] = 期望.rstrip('\n')
                条目['updatedAt'] = int(time.time() * 1000)
            文本 = json.dumps(数据, ensure_ascii=False, indent=2 if 缩进 is None else 缩进)
            写入文件(文件, 文本 + '\n', 目标换行符(文件, 文本))
        当前值, _ = 读取目标当前值(目标)
    except Exception as e:
        return False, '写入或校验异常: %s（备份保留在 %s）' % (e, 备份)
    if 当前值 is None or 归一(当前值).rstrip('\n') != 期望.rstrip('\n'):
        return False, '写入后校验不一致（备份保留在 %s）' % 备份
    注明 = '；原 JSON 缩进风格未识别，按默认 2 空格整文件重写' if 原文本 is not None and 缩进 is None else ''
    return True, '已备份至 %s%s' % (os.path.basename(备份), 注明)


def main():
    参数 = sys.argv[1:]
    模式 = 参数[0] if 参数 else 'check'
    指定 = 参数[1:] if 模式 == 'apply' and 参数 else []
    if 模式 not in ('check', 'apply'):
        print('未知模式: %s（仅支持 check / apply）' % 模式)
        return 2
    try:
        配置 = 读取JSON(os.path.join(配置目录, '同步配置.json'))
        配置['源文件'] = 解析路径(配置['源文件'])
        for 目标 in 配置['目标']:
            目标['文件'] = 解析路径(目标['文件'])
        正文 = 读取源正文(配置['源文件'])
    except (体系错误, ValueError, KeyError, TypeError, OSError) as e:
        # 配置或源文件损坏按运行错误退出；ValueError 涵盖 JSONDecodeError 与 UnicodeDecodeError
        print('错误: %s' % (e if isinstance(e, 体系错误) else '%s: %s' % (type(e).__name__, e)))
        return 2

    有差异 = False
    有失败 = False
    print('模式: %s | 源: %s | 正文 %d 字符\n' % (模式, 配置['源文件'], len(正文)))
    for 目标 in 配置['目标']:
        名称 = 目标['名称']
        if 指定 and 名称 not in 指定:
            continue
        try:
            期望 = 期望内容(目标, 正文)
            当前值, 数据 = 读取目标当前值(目标)
            if 当前值 is None:
                状态, 摘要 = '缺失', '目标文件或字段不存在，apply 时将补建'
            elif 归一(当前值).rstrip('\n') == 期望.rstrip('\n'):
                状态, 摘要 = '一致', ''
            else:
                状态, 摘要 = '需更新', 差异摘要(当前值, 期望)
            if 模式 == 'apply' and 状态 in ('需更新', '缺失'):
                成功, 说明 = 应用目标(目标, 期望, 数据)
                状态 = '已更新' if 成功 else '失败'
                摘要 = (摘要 + '；' if 摘要 else '') + 说明
                有失败 = 有失败 or not 成功
            else:
                有差异 = 有差异 or 状态 == '需更新' or 状态 == '缺失'
            print('[%s] %s → %s' % (状态, 名称, 目标['文件']))
            if 摘要:
                print('      %s' % 摘要)
        except 体系错误 as e:
            有失败 = True
            print('[失败] %s → %s' % (名称, e))
    return 1 if (有差异 or 有失败) else 0


if __name__ == '__main__':
    sys.exit(main())
