"""Discover completed manual results at their real scope; optionally show copies.

No job is submitted, cancelled, moved, or edited. --apply copies only completed
and hash-verified files into independent result nodes of the bound notebook.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import time
from urllib.parse import quote

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).with_name('shensi_bridge.py'))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
draft_spec = importlib.util.spec_from_file_location('result_drafts', Path(__file__).with_name('restore_video_result_drafts.py'))
result_drafts = importlib.util.module_from_spec(draft_spec)
draft_spec.loader.exec_module(result_drafts)


def sha_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_file(workspace, relative):
    root = Path(workspace).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('成片文件不存在或超出其真实工作区：' + relative)
    return path


def snapshot(loaded, document_id):
    state = loaded['state']
    return {'stateStamp': loaded['stateStamp'], 'savedAt': state.get('savedAt'),
        'documentId': document_id, 'document': state['documents'][document_id],
        'workspaceAssets': [a for a in state.get('workspaceAssets', []) if a.get('sourceDocumentId') == document_id]}


def append_asset(existing, record):
    result = copy.deepcopy(existing or [])
    index = next((i for i, a in enumerate(result)
                  if a.get('sourceDocumentId') == record['sourceDocumentId']
                  and (a.get('id') == record['id']
                       or (a.get('generationJobId') == record['generationJobId']
                           and a.get('attachment', {}).get('sha256') == record['attachment']['sha256']))), None)
    if index is None:
        result.append(record)
    else:
        result[index] = {**result[index], **record, 'id': result[index]['id']}
    return result


def original_generation(job):
    """Keep reproducible public request fields without exporting runtime secrets."""
    request = job.get('request') or {}
    fields = ('prompt', 'duration', 'aspectRatio', 'resolution', 'generationMode',
              'generateAudio', 'videoCount', 'referenceOrder',
              'providerPromptReferenceTokens', 'promptReferenceSequence', 'preserveReferenceTokens')
    result = {key: copy.deepcopy(request[key]) for key in fields if key in request}
    media_fields = ('id', 'relativePath', 'name', 'mimeType', 'sha256', 'objectHash',
                    'size', 'referenceRole', 'durationSeconds', 'durationMs',
                    'imageWidth', 'imageHeight', 'videoWidth', 'videoHeight')
    result['referenceMedia'] = [{key: copy.deepcopy(media[key]) for key in media_fields if key in media}
                                for media in request.get('referenceMedia') or [] if isinstance(media, dict)]
    result['profile'] = {key: value for key, value in (request.get('settings') or {}).items()
                         if key in bridge.PROFILE_FIELDS}
    connection_id = ((request.get('settings') or {}).get('connectionId')
                     or (request.get('settings') or {}).get('id')
                     or (request.get('generationProfile') or {}).get('connectionId'))
    if connection_id:
        result['profile']['connectionId'] = connection_id
    return result


def result_position(anchor, nodes, width, height):
    """Place later manual versions beside the navigator without covering a prior result."""
    x = anchor.get('x', 1360) + anchor.get('width', 700) + 160
    y = anchor.get('y', 80)
    for _ in range(len(nodes) + 1):
        overlaps = [node for node in nodes
                    if x < node.get('x', 0) + node.get('width', 0) + 40
                    and x + width + 40 > node.get('x', 0)
                    and y < node.get('y', 0) + node.get('height', 0) + 40
                    and y + height + 40 > node.get('y', 0)]
        if not overlaps:
            return x, y
        y = max(node.get('y', 0) + node.get('height', 0) + 80 for node in overlaps)
    raise ValueError('无法为成片找到独立空白位置')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin')
    parser.add_argument('--whiteboard-binding', help='含source/target工作区与documentId的迁移绑定')
    parser.add_argument('--source-binding', help='也可分别提供源/目标工作区绑定及documentId')
    parser.add_argument('--target-binding')
    parser.add_argument('--source-document-id')
    parser.add_argument('--target-document-id')
    parser.add_argument('--result-node-prefix', default='chushou-manual-video')
    parser.add_argument('--out-dir')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--drafts-only', action='store_true', help='仅核对／恢复已存在独立结果的操作栏，不写画布或复制文件')
    parser.add_argument('--repair-existing-result-drafts', action='store_true', help='明确恢复结果操作栏原参数；默认保留用户已有二次编辑')
    args = parser.parse_args()
    binding_file = Path(args.whiteboard_binding).resolve() if args.whiteboard_binding else None
    if binding_file:
        binding = bridge.read_json(binding_file)
        base_directory = binding_file.parent
    else:
        if not args.source_binding or not args.target_binding:
            raise ValueError('需提供whiteboard-binding，或source-binding与target-binding')
        first, second = bridge.read_json(args.source_binding), bridge.read_json(args.target_binding)
        binding = {'sourceWorkspacePath': first['workspacePath'], 'workspacePath': second['workspacePath'],
            'sourceDocumentId': args.source_document_id or first.get('documentId'),
            'documentId': args.target_document_id or second.get('documentId')}
        base_directory = Path(args.target_binding).resolve().parent
    if args.source_document_id:
        binding['sourceDocumentId'] = args.source_document_id
    if args.target_document_id:
        binding['documentId'] = args.target_document_id
    if not binding.get('sourceDocumentId') or not binding.get('documentId'):
        raise ValueError('缺少源或目标documentId')
    out = Path(args.out_dir) if args.out_dir else base_directory / 'manual-result-recovery' / str(int(time.time()*1000))
    out.mkdir(parents=True, exist_ok=True)
    source_workspace, source_document_id = binding['sourceWorkspacePath'], binding['sourceDocumentId']
    target_workspace, document_id = binding['workspacePath'], binding['documentId']
    client = bridge.Shensi(args.origin)
    loaded = client.api('/api/workspace/load', {'workspacePath': target_workspace, 'fresh': True})
    if loaded['state'].get('workspaceKind') != 'notebook':
        raise ValueError('目标必须是真实笔记工作区；作品结果仍保留在原scope')
    bridge.write_json(out / 'notebook-before.json', snapshot(loaded, document_id))
    document = loaded['state']['documents'][document_id]
    jobs = client.api('/api/generation/jobs?includeApplied=true&workspacePath=' + quote(source_workspace, safe='')).get('jobs', [])
    jobs = [job for job in jobs if job.get('channel') == 'video'
            and job.get('target', {}).get('documentId') == source_document_id
            and Path(job.get('target', {}).get('workspacePath', '')).resolve() == Path(source_workspace).resolve()]
    jobs.sort(key=lambda job: job.get('createdAt', ''))
    public = []
    hash_nodes = {}
    history_by_hash = {}
    for asset in document.get('canvas', {}).get('assets', []):
        if asset.get('kind') == 'video' and asset.get('attachment', {}).get('sha256'):
            history_by_hash[asset['attachment']['sha256']] = asset
    for node in document['canvas']['nodes']:
        if node.get('kind') == 'video' and node.get('file'):
            digest = sha_file(safe_file(target_workspace, node['file']))
            hash_nodes.setdefault(digest, node)
    imports, pending, terminal = [], [], []
    for job in jobs:
        if job.get('status') != 'complete':
            record = {'jobId': job['id'], 'status': job.get('status'), 'target': job.get('target')}
            (terminal if job.get('status') in bridge.TERMINAL else pending).append(record)
            continue
        result = job.get('result') or {}
        attachments = [result.get('attachment')] + list(result.get('attachments') or [])
        seen = set()
        for attachment in attachments:
            if not isinstance(attachment, dict) or not attachment.get('relativePath'):
                continue
            digest = attachment.get('sha256') or attachment.get('objectHash')
            if not digest or digest in seen:
                continue
            seen.add(digest)
            file = safe_file(source_workspace, attachment['relativePath'])
            if sha_file(file) != digest:
                raise ValueError('原成片SHA256不一致：' + job['id'])
            old_node = hash_nodes.get(digest)
            row = {'jobId': job['id'], 'createdAt': job.get('createdAt'), 'completedAt': job.get('completedAt'),
                'realTarget': job['target'], 'sourceFile': str(file), 'sha256': digest,
                'bytes': file.stat().st_size, 'alreadyVisibleInNotebook': bool(old_node),
                'originalGeneration': original_generation(job)}
            public.append(row)
            imports.append((job, attachment, digest, old_node))
    bridge.write_json(out / 'discovered-results.json', {'results': public, 'pendingJobsKeptAtOriginalScope': pending,
        'unsuccessfulTerminalJobs': terminal})
    if not args.apply:
        drafts = result_drafts.restore_result_drafts(client, source_workspace, target_workspace, document_id,
            [(job, old_node['id']) for job, _, _, old_node in imports if old_node], out / 'result-composers',
            repair_existing=args.repair_existing_result_drafts)
        print(json.dumps({'results': [{key: value for key, value in item.items() if key != 'originalGeneration'}
                                     for item in public], 'pendingJobsKeptAtOriginalScope': pending,
                          'unsuccessfulTerminalJobs': terminal, 'resultDrafts': drafts, 'applied': False}, ensure_ascii=False, indent=2))
        return
    if args.drafts_only and any(not old_node for _, _, _, old_node in imports):
        raise ValueError('存在尚未显示的成片；drafts-only禁止复制或新建节点，需先执行成片补同步')
    copied_attachments = {}
    for job, attachment, digest, old_node in imports:
        if old_node:
            continue
        copy_file = out / ('attachment-copy-' + job['id'] + '.json')
        request = {'sourceWorkspacePath': source_workspace, 'targetWorkspacePath': target_workspace,
            'relativePath': attachment['relativePath'], 'name': attachment.get('name'),
            'mimeType': attachment.get('mimeType', 'video/mp4')}
        copied = client.api('/api/workspace/attachment/copy', request)['attachment']
        copied_file = safe_file(target_workspace, copied['relativePath'])
        if sha_file(copied_file) != digest:
            raise ValueError('笔记成片副本SHA256不一致：' + job['id'])
        copied_attachments[digest] = copied
        bridge.write_json(copy_file, {'request': request, 'attachment': copied, 'sha256Verified': True})
    # Fresh state after attachment copies; retain all concurrent user changes.
    latest = client.api('/api/workspace/load', {'workspacePath': target_workspace, 'fresh': True})
    state = latest['state']
    state_before = copy.deepcopy(state)
    document = state['documents'][document_id]
    nodes = {node['id']: node for node in document['canvas']['nodes']}
    report = []
    for job, original, digest, old_node in imports:
        match = re.search(r'(?:segment|shot|clip)-(\d+)$', job['target'].get('nodeId', ''))
        number = int(match[1]) if match else 0
        if old_node:
            node = nodes[old_node['id']]
            attachment = copy.deepcopy(history_by_hash.get(digest, {}).get('attachment', {}))
            attachment.update(relativePath=node['file'], mimeType=node.get('mimeType', 'video/mp4'),
                name=Path(node['file']).name, sha256=digest, objectHash=digest,
                size=safe_file(target_workspace, node['file']).stat().st_size)
        else:
            attachment = copied_attachments[digest]
            node_id = args.result_node_prefix + '-' + job['id'].removeprefix('generation-') + '-' + digest[:10]
            anchor = nodes.get(job['target'].get('nodeId'), {})
            ratio = (attachment.get('videoWidth') or original.get('videoWidth') or 16) / (attachment.get('videoHeight') or original.get('videoHeight') or 9)
            x, y = result_position(anchor, nodes.values(), 420, 420 / ratio)
            label = f'第{number:02d}段 · 手动生成成片' if number else (job['target'].get('nodeName') or '手动生成成片')
            node = {'id': node_id, 'type': 'file', 'kind': 'video', 'text': '',
                'name': label, 'file': attachment['relativePath'],
                'mimeType': attachment.get('mimeType', 'video/mp4'), 'aspectRatio': ratio,
                'x': x, 'y': y, 'width': 420, 'height': 420 / ratio}
            for field in ('thumbnailRelativePath', 'thumbnailMimeType', 'durationMs'):
                if attachment.get(field):
                    node[field] = attachment[field]
            document['canvas']['nodes'].append(node)
            nodes[node_id] = node
        scope_digest = hashlib.sha256(document_id.encode()).hexdigest()[:8]
        asset = {'id': args.result_node_prefix + '-history-' + scope_digest + '-' + job['id'] + '-' + digest[:10], 'kind': 'video', 'origin': 'generated',
            'source': 'whiteboard', 'sourceNodeId': node['id'], 'sourceDocumentId': document_id,
            'nodeName': node.get('name', '手动生成成片'), 'attachment': attachment,
            'generationJobId': job['id'], 'prompt': job.get('request', {}).get('prompt', ''),
            'createdAt': job.get('completedAt') or job.get('createdAt'),
            'completedAt': job.get('completedAt'), 'aspectRatio': node.get('aspectRatio', 16/9)}
        if not args.drafts_only:
            document['canvas']['assets'] = append_asset(document['canvas'].get('assets', []), asset)
            state['workspaceAssets'] = append_asset(state.get('workspaceAssets', []), asset)
        report.append({'jobId': job['id'], 'realTarget': job['target'], 'notebookResultNodeId': node['id'],
            'notebookFile': str(safe_file(target_workspace, node['file'])), 'notebookRelativePath': node['file'],
            'sha256': digest, 'sha256Verified': True, 'existingResultReused': bool(old_node),
            'originalGeneration': original_generation(job)})
    workspace_saved = state != state_before
    if workspace_saved:
        client.api('/api/workspace/save', {'workspacePath': target_workspace, 'state': state,
            'expectedStateStamp': latest['stateStamp'], 'operationDocumentIds': [document_id]})
    after = client.api('/api/workspace/load', {'workspacePath': target_workspace, 'fresh': True})
    bridge.write_json(out / 'notebook-after.json', snapshot(after, document_id))
    readback = {n['id']: n for n in after['state']['documents'][document_id]['canvas']['nodes']}
    for item in report:
        node = readback[item['notebookResultNodeId']]
        if node['file'] != item['notebookRelativePath'] or sha_file(safe_file(target_workspace, node['file'])) != item['sha256']:
            raise ValueError('成片持久回读失败：' + item['jobId'])
    original_jobs = {job['id']: job for job, _, _, _ in imports}
    draft_result = result_drafts.restore_result_drafts(client, source_workspace, target_workspace, document_id,
        [(original_jobs[item['jobId']], item['notebookResultNodeId']) for item in report], out / 'result-composers',
        apply=True, repair_existing=args.repair_existing_result_drafts)
    result = {'sourceWorkspacePath': source_workspace, 'notebookWorkspacePath': target_workspace,
        'notebookDocumentId': document_id, 'completedResults': report, 'pendingJobsKeptAtOriginalScope': pending,
        'unsuccessfulTerminalJobs': terminal,
        'jobsChanged': False, 'canvasRebuilt': False, 'draftsChanged': draft_result['applied'], 'generationSubmitted': False,
        'resultDrafts': draft_result,
        'workspaceSaved': workspace_saved,
        'persistenceReadback': 'passed', 'reportDirectory': str(out), 'syncedAt': bridge.now()}
    bridge.write_json(out / 'result-sync-report.json', result)
    binding['manualResultSyncReportFile'] = str(out / 'result-sync-report.json')
    binding['manualResults'] = report
    binding['pendingSourceVideoJobs'] = pending
    if binding_file:
        bridge.write_json(binding_file, binding)
    else:
        bridge.write_json(out / 'result-sync-binding.json', binding)
    display = copy.deepcopy(result)
    for item in display['completedResults']:
        item.pop('originalGeneration', None)
    print(json.dumps(display, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        raise SystemExit(1)
