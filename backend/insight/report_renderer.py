"""Standalone HTML from the application's shared React report (no model/API calls)."""
import json
import os
import shutil
import subprocess
from . import config

class ReportRenderFailure(RuntimeError):
    pass

def render_html(report,view='brief',version=None):
    if view not in ('brief','full'):raise ValueError('Unsupported report view')
    renderer=config.ROOT/'frontend/dist/report-renderer.cjs'
    if not renderer.exists():
        raise ReportRenderFailure('打印模板尚未构建，请在项目目录执行 npm --prefix frontend run build 后重试。')
    node=os.getenv('REPORT_NODE_PATH') or shutil.which('node')
    if not node:
        raise ReportRenderFailure('打印导出需要本机 Node.js；请安装 Node.js 或在后端配置 REPORT_NODE_PATH。')
    # Data uses stdin, never shell interpolation, command arguments, or logs.
    try:
        output=subprocess.run([node,str(renderer)],input=json.dumps({'report':report,'view':view,'version':version},ensure_ascii=False),
            text=True,encoding='utf-8',capture_output=True,timeout=15,shell=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise ReportRenderFailure('共享报告渲染器启动失败或超时，请检查 Node.js 与前端构建。') from exc
    if output.returncode or not output.stdout.startswith('<!doctype html>'):
        # Do not expose renderer stderr: it could include source comments.
        raise ReportRenderFailure('共享报告模板渲染失败；请更新前端构建后重试，原始报告数据已保留。')
    return output.stdout
