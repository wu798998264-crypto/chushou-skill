"""Copy one reviewed Shensi whiteboard into a notebook, including native drafts.

Default is read-only planning. --apply creates/reuses the named notebook and
uses Shensi's native document COPY endpoint. Never submits or retargets jobs.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace
from urllib.parse import quote, urlencode

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).with_name('shensi_bridge.py'))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
MEDIA_KEYS = {'file', 'relativePath', 'thumbnailRelativePath', 'attachmentPath'}
VALUE_FIELDS = {'prompt', 'instruction', 'connectionId', 'model', 'aspectRatio', 'duration', 'resolution',
    'quality', 'generationMode', 'generateAudio', 'background', 'imageCount', 'videoCount',
    'explicitReferences', 'referenceOrder', 'promptReferenceSequence', 'includeTargetReference',
    'multiframeTransitions', 'audioType', 'scene', 'voiceId', 'language', 'speed', 'format', 'sampleRate'}


def media_references(value, location=()):
    result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            where = location + (str(key),)
            if key in MEDIA_KEYS and isinstance(child, str) and child.strip():
                path = child.strip().replace('\\', '/')
                if not re.match(r'^[a-z][a-z\d+.-]*:|^//|^/api/', path, re.I):
                    result['/'.join(where)] = path
            elif isinstance(child, (dict, list)):
                result.update(media_references(child, where))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(media_references(child, location + (str(index),)))
    return result


def checked_file(workspace, relative_path):
    root = Path(workspace).resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('附件不存在或超出工作区：' + relative_path)
    return path


def digest_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_copy(source_workspace, target_workspace, source_payload, target_payload):
    old, new = media_references(source_payload), media_references(target_payload)
    if set(old) != set(new):
        raise ValueError('原生复制后的附件字段不完整，保留源作品及目标副本，停止切换绑定')
    pairs, checked = [], {}
    for address, source_path in old.items():
        target_path = new[address]
        if (source_path, target_path) not in checked:
            first, second = checked_file(source_workspace, source_path), checked_file(target_workspace, target_path)
            source_sha, target_sha = digest_file(first), digest_file(second)
            if source_sha != target_sha:
                raise ValueError('复制附件SHA256不一致：' + source_path)
            checked[source_path, target_path] = {'sourceRelativePath': source_path,
                'targetRelativePath': target_path, 'sha256': source_sha, 'bytes': second.stat().st_size}
        pairs.append({'field': address, **checked[source_path, target_path]})
    return {'status': 'passed', 'referenceFieldCount': len(pairs), 'uniqueFileCount': len(checked), 'files': pairs}


def matching_board(state, source_document, preferred_id=''):
    nodes = {node['id'] for node in source_document.get('canvas', {}).get('nodes', [])}
    if not nodes:
        raise ValueError('源白板没有可复制节点')
    candidates = []
    for document_id, document in state.get('documents', {}).items():
        target_ids = {node['id'] for node in document.get('canvas', {}).get('nodes', [])}
        if document_id == preferred_id and document.get('documentKind') == 'whiteboard' and nodes.issubset(target_ids):
            return document_id
        if (document.get('documentKind') == 'whiteboard'
                and {item for item in target_ids if not item.startswith('kunshou-01-preserved-result-')} == nodes):
            candidates.append(document_id)
    if preferred_id in candidates:
        return preferred_id
    if len(candidates) > 1:
        raise ValueError('目标有多个相同节点的白板，请指定既有迁移绑定，不重复复制')
    return candidates[0] if candidates else None


def converted_drafts(source_cache, target_cache, source_document_id, target_document_id, target_workspace):
    cache = copy.deepcopy(target_cache)
    cache.setdefault('entries', {})
    workspace_id = 'notebook:' + target_workspace.strip().lower()
    records, stamp = [], int(time.time() * 1000)
    for source_key, entry in source_cache.get('entries', {}).items():
        if entry.get('documentId') != source_document_id:
            continue
        scope = {'workspaceId': workspace_id, 'documentId': target_document_id,
                 'nodeId': entry['nodeId'], 'channel': entry['channel']}
        key = '::'.join(quote(scope[k], safe="~()*!.'-") for k in ('workspaceId', 'documentId', 'nodeId', 'channel'))
        values = {k: v for k, v in entry.get('values', {}).items() if k in VALUE_FIELDS}
        bridge.reject_secrets(values)
        old = cache['entries'].get(key)
        # A second run must not replace edits the author made in the notebook.
        retained = bool(old and old.get('updatedAt', 0) >= entry.get('updatedAt', 0))
        if not retained:
            cache['entries'][key] = {**scope, 'values': values, 'updatedAt': stamp}
        records.append({'sourceKey': source_key, 'targetKey': key, 'nodeId': entry['nodeId'],
                        'channel': entry['channel'], 'sourceUpdatedAt': entry.get('updatedAt', 0),
                        'retainedNewerNotebookDraft': retained})
    # Keep the target's current active workbench. Never transplant project bar 6.
    return cache, records, stamp


def fix_asset_scope(document, old_document_id, new_document_id, target_workspace):
    changed = False
    for asset in document.get('canvas', {}).get('assets', []):
        if asset.get('sourceDocumentId') in (None, '', old_document_id):
            next_fields = {'sourceDocumentId': new_document_id, 'sourceWorkspaceKind': 'notebook',
                           'sourceWorkspacePath': target_workspace}
            if any(asset.get(k) != v for k, v in next_fields.items()):
                asset.update(next_fields)
                changed = True
    return changed


def separate_copied_results(document):
    """Keep real copied files on separate nodes; blank targets do not own old jobs."""
    canvas = document['canvas']
    existing = {node['id']: node for node in canvas['nodes']}
    records, additions, changed = [], [], False
    for node in canvas['nodes']:
        match = re.fullmatch(r'kunshou-01-segment-(\d{2})', node['id'])
        if not match or not node.get('file') or node.get('kind') != 'video':
            continue
        same_file = next((candidate for candidate in canvas['nodes']
                          if candidate.get('file') == node['file']
                          and not re.fullmatch(r'kunshou-01-segment-\d{2}', candidate['id'])), None)
        result_id = same_file['id'] if same_file else 'kunshou-01-preserved-result-' + match[1]
        if result_id in existing and existing[result_id].get('file') != node['file']:
            job_suffix = str(node.get('generation', {}).get('jobId') or hashlib.sha256(node['file'].encode()).hexdigest())[-12:]
            result_id += '-' + job_suffix
        result_node = {**copy.deepcopy(node), 'id': result_id,
            'name': f'第{match[1]}段 · 原作品成片（保留）', 'text': '',
            'x': node.get('x', 1360) + node.get('width', 700) + 120}
        generation = result_node.pop('generation', None)
        result_node.pop('generationIntent', None)
        if result_id not in existing:
            additions.append(result_node)
        records.append({'segment': int(match[1]), 'sourceStableNodeId': node['id'], 'copiedResultNodeId': result_id,
            'relativePath': node['file'], 'originalJobId': (generation or {}).get('jobId'),
            'originalGeneration': generation, 'newJobCreated': False})
        for asset in canvas.get('assets', []):
            if asset.get('sourceNodeId') == node['id'] and asset.get('attachment', {}).get('relativePath') == node['file']:
                asset['sourceNodeId'] = result_id
        for field in ('file', 'mimeType', 'generation', 'thumbnailRelativePath', 'thumbnailMimeType', 'durationMs',
                      'mediaBatchDirectory', 'mediaBatchIndexPath', 'whiteboardMediaDirectory', 'whiteboardMediaIndexPath'):
            node.pop(field, None)
        node.update(type='text', kind='text', generationIntent={'channel': 'video'})
        changed = True
    canvas['nodes'].extend(additions)
    return records, changed


def detach_archived_video_targets(document, workspace_assets, document_id, workspace):
    """A blank video target must not own copied source generation history.

    Shensi's synchronizeLegacyWhiteboardAssetCards otherwise restores these
    archived job results into the blank card during ensureStateSchema/startup.
    Reuse actual file/SHA result nodes; never delete history or retarget jobs.
    """
    canvas = document['canvas']
    file_nodes, hash_nodes, remapped = {}, {}, []
    for node in canvas['nodes']:
        if node.get('kind') != 'video' or not node.get('file') or re.fullmatch(r'kunshou-01-segment-\d{2}', node['id']):
            continue
        digest = digest_file(checked_file(workspace, node['file']))
        file_nodes.setdefault(node['file'], node)
        hash_nodes.setdefault(digest, node)
    assets = [('canvas', asset) for asset in canvas.get('assets', [])]
    assets += [('workspace', asset) for asset in workspace_assets or []
               if asset.get('sourceDocumentId') in (None, '', document_id)]
    for container, asset in assets:
        match = re.fullmatch(r'kunshou-01-segment-(\d{2})', str(asset.get('sourceNodeId', '')))
        if not match or asset.get('kind') != 'video' or asset.get('origin') != 'generated' or not asset.get('generationJobId'):
            continue
        attachment = asset.get('attachment') or {}
        path = attachment.get('relativePath')
        if not path:
            raise ValueError('历史视频缺少实际附件，禁止丢弃：' + asset['id'])
        digest = digest_file(checked_file(workspace, path))
        declared = attachment.get('sha256') or attachment.get('objectHash')
        if declared and declared != digest:
            raise ValueError('历史视频SHA不一致：' + asset['id'])
        result = file_nodes.get(path) or hash_nodes.get(digest)
        if not result:
            stable = next((node for node in canvas['nodes'] if node['id'] == asset['sourceNodeId']), {})
            result_id = f'kunshou-01-preserved-history-{match[1]}-{digest[:12]}'
            result = {'id': result_id, 'type': 'file', 'kind': 'video',
                'name': f'第{match[1]}段 · 历史成片（保留）', 'text': '', 'file': path,
                'mimeType': attachment.get('mimeType', 'video/mp4'), 'aspectRatio': asset.get('aspectRatio', 16/9),
                'x': stable.get('x', 1360) + stable.get('width', 700) + 120,
                'y': stable.get('y', 80) + 240 * (len(hash_nodes) + 1), 'width': 420, 'height': 420/(16/9)}
            for field in ('thumbnailRelativePath', 'thumbnailMimeType', 'durationMs'):
                if attachment.get(field):
                    result[field] = attachment[field]
            canvas['nodes'].append(result)
            file_nodes[path] = result
            hash_nodes[digest] = result
        old_id = asset['sourceNodeId']
        asset.update(sourceNodeId=result['id'], nodeName=result.get('name', asset.get('nodeName', '')))
        remapped.append({'container': container, 'assetId': asset['id'], 'originalSourceNodeId': old_id,
            'preservedNodeId': result['id'], 'generationJobId': asset['generationJobId'],
            'relativePath': path, 'sha256': digest})
    return remapped


def source_layout_for_verification(document, source_document):
    """Reconstruct only attachment field positions after separate-result conversion."""
    result = copy.deepcopy(document)
    all_nodes = {node['id']: node for node in result['canvas']['nodes']}
    result['canvas']['nodes'] = [node for node in result['canvas']['nodes']
                                if not node['id'].startswith('kunshou-01-preserved-result-')]
    for original in source_document.get('canvas', {}).get('nodes', []):
        if original.get('file') and original['id'].startswith('kunshou-01-segment-'):
            number = original['id'].rsplit('-', 1)[1]
            preserved = all_nodes.get('kunshou-01-preserved-result-' + number)
            target = all_nodes.get(original['id'])
            if preserved and target:
                for field in MEDIA_KEYS:
                    if field in original and field in preserved:
                        target[field] = preserved[field]
    return result


def merge_history(existing, additions):
    result = copy.deepcopy(existing or [])
    by_id = {record['id']: index for index, record in enumerate(result)}
    for item in additions:
        index = by_id.get(item['id'])
        if index is None:
            by_id[item['id']] = len(result)
            result.append(copy.deepcopy(item))
        else:
            result[index] = {**result[index], **item}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin')
    parser.add_argument('--source-binding', required=True)
    parser.add_argument('--episode-dir', required=True)
    parser.add_argument('--source-document-id', required=True, help='当前源工作区中实际白板ID')
    parser.add_argument('--notebook-name', default='触手·视频复刻')
    parser.add_argument('--notebook-binding', help='默认为本项目output/shensi/notebook-workspace-binding.json')
    parser.add_argument('--app-root')
    parser.add_argument('--apply', action='store_true', help='执行原生复制；省略时仅只读规划')
    args = parser.parse_args()
    client = bridge.Shensi(args.origin)
    episode = Path(args.episode_dir).resolve()
    out = episode / 'shensi'
    source_binding = bridge.read_json(args.source_binding)
    source_workspace = source_binding['workspacePath']
    source_loaded = client.api('/api/workspace/load', {'workspacePath': source_workspace, 'fresh': True})
    source_state = source_loaded.get('state') or {}
    source_document = source_state.get('documents', {}).get(args.source_document_id)
    if not source_document or source_document.get('documentKind') != 'whiteboard':
        raise ValueError('源白板不存在，不能复制')
    source_kind = source_state.get('workspaceKind', 'project')
    source_payload = {'document': source_document, 'histories': source_state.get('histories', {}).get(args.source_document_id, [])}
    source_refs = media_references(source_payload)
    source_files = {path: {'sha256': digest_file(checked_file(source_workspace, path)),
                          'bytes': checked_file(source_workspace, path).stat().st_size}
                    for path in set(source_refs.values())}
    query = urlencode({'workspacePath': source_workspace, 'workspaceKind': source_kind})
    source_drafts = client.api('/api/whiteboard/generation-drafts?' + query)['cache']
    selected = [entry for entry in source_drafts.get('entries', {}).values()
                if entry.get('documentId') == args.source_document_id]
    plan = {'sourceWorkspacePath': source_workspace, 'sourceWorkspaceKind': source_kind,
        'sourceDocumentId': args.source_document_id, 'sourceTitle': source_document['title'],
        'notebookName': args.notebook_name, 'operation': 'copy', 'sourcePreserved': True,
        'jobsRetargeted': False, 'sourceNodeCount': len(source_document['canvas']['nodes']),
        'sourceFileCount': len(source_files), 'draftCount': len(selected),
        'draftCountsByChannel': {channel: sum(e['channel'] == channel for e in selected)
                               for channel in ('image', 'video', 'text', 'audio')},
        'files': source_files, 'plannedAt': bridge.now(), 'serviceMutationExecuted': False}
    bridge.write_json(out / 'notebook-migration-plan.json', plan)
    if not args.apply:
        print(json.dumps({k: v for k, v in plan.items() if k != 'files'}, ensure_ascii=False, indent=2))
        return

    notebook_binding_file = Path(args.notebook_binding) if args.notebook_binding else Path(args.source_binding).with_name('notebook-workspace-binding.json')
    operation_id = source_binding.get('operationId', 'chushou') + '-notebook-video-replication'
    notebook_binding = bridge.workspace(client, SimpleNamespace(name=args.notebook_name, operation_id=operation_id,
        kind='notebook', out=str(notebook_binding_file), app_root=args.app_root))
    target_workspace = notebook_binding['workspacePath']
    if Path(source_workspace).resolve() == Path(target_workspace).resolve():
        raise ValueError('目标笔记本与源作品路径相同，禁止改写原作品')
    target_loaded = client.api('/api/workspace/load', {'workspacePath': target_workspace, 'fresh': True})
    target_state = target_loaded['state']
    if target_state.get('workspaceKind') != 'notebook':
        raise ValueError('目标真实模式不是笔记')
    receipt_file = out / 'notebook-transfer-receipt.json'
    binding_file = out / 'notebook-whiteboard-binding.json'
    previous = bridge.read_json(binding_file) if binding_file.exists() else {}
    preferred_id = previous.get('documentId', '') if previous.get('workspacePath') == target_workspace else ''
    target_document_id = matching_board(target_state, source_document, preferred_id)
    if not target_document_id:
        receipt = bridge.read_json(receipt_file) if receipt_file.exists() else {}
        if receipt.get('state') in ('copying', 'outcome_unknown'):
            raise ValueError('前次复制结果仍未确认；保留回执，仅查询目标，不重复复制')
        request = {'operation': 'copy', 'sourceWorkspacePath': source_workspace,
            'documentIds': [args.source_document_id], 'targetWorkspacePath': target_workspace,
            'targetWorkspaceKind': 'notebook', 'targetModuleId': 'library',
            'targetViewId': 'default', 'targetLocationId': 'library:default:root'}
        receipt = {'state': 'copying', 'request': request, 'startedAt': bridge.now()}
        bridge.write_json(receipt_file, receipt)
        try:
            copied = client.api('/api/workspaces/documents/transfer', request)
        except Exception:
            receipt.update(state='outcome_unknown', updatedAt=bridge.now())
            bridge.write_json(receipt_file, receipt)
            raise
        receipt.update(state='copied', completedAt=bridge.now(), transferred=copied['transferred'],
            copiedAttachmentCount=copied.get('copiedAttachmentCount'),
            targetStateStamp=copied.get('targetStateStamp'))
        bridge.write_json(receipt_file, receipt)
        target_document_id = next(item['targetDocumentId'] for item in copied['transferred']
                                  if item['sourceDocumentId'] == args.source_document_id)
        target_loaded = client.api('/api/workspace/load', {'workspacePath': target_workspace, 'fresh': True})
        target_state = target_loaded['state']
    target_document = target_state['documents'][target_document_id]
    target_payload = {'document': source_layout_for_verification(target_document, source_document),
                      'histories': target_state.get('histories', {}).get(target_document_id, [])}
    verification = verify_copy(source_workspace, target_workspace, source_payload, target_payload)
    bridge.write_json(out / 'notebook-attachment-verification.json', verification)
    if receipt_file.exists():
        recovered_receipt = bridge.read_json(receipt_file)
        if recovered_receipt.get('state') in ('copying', 'outcome_unknown'):
            recovered_receipt.update(state='recovered-and-verified', recoveredAt=bridge.now(),
                transferred=[{'sourceDocumentId': args.source_document_id,
                    'targetDocumentId': target_document_id, 'title': target_document['title']}],
                verifiedUniqueFileCount=verification['uniqueFileCount'], duplicateCopySubmitted=False)
            bridge.write_json(receipt_file, recovered_receipt)

    preserved_results, results_changed = separate_copied_results(target_document)
    if not preserved_results:
        preserved_results = previous.get('preservedResults', [])
    archived_remaps = detach_archived_video_targets(target_document, target_state.get('workspaceAssets', []),
                                                    target_document_id, target_workspace)
    changed = fix_asset_scope(target_document, args.source_document_id, target_document_id, target_workspace) or results_changed or bool(archived_remaps)
    new_history = merge_history(target_state.get('workspaceAssets', []), target_document.get('canvas', {}).get('assets', []))
    if changed or new_history != target_state.get('workspaceAssets', []):
        target_state['workspaceAssets'] = new_history
        client.api('/api/workspace/save', {'workspacePath': target_workspace, 'state': target_state,
            'expectedStateStamp': target_loaded['stateStamp'], 'operationDocumentIds': [target_document_id]})
    target_query = urlencode({'workspacePath': target_workspace, 'workspaceKind': 'notebook'})
    target_cache = client.api('/api/whiteboard/generation-drafts?' + target_query)['cache']
    cache, mappings, stamp = converted_drafts(source_drafts, target_cache, args.source_document_id,
                                              target_document_id, target_workspace)
    node_ids = {node['id'] for node in target_document['canvas']['nodes']}
    for mapping in mappings:
        entry = cache['entries'][mapping['targetKey']]
        if entry['nodeId'] not in node_ids:
            raise ValueError('迁移草稿目标节点不存在：' + entry['nodeId'])
        for field in ('explicitReferences', 'referenceOrder', 'promptReferenceSequence'):
            refs = [r for r in str(entry['values'].get(field, '')).split(',') if r]
            if not set(refs).issubset(node_ids):
                raise ValueError('迁移草稿上游引用不完整：' + entry['nodeId'])
    client.api('/api/whiteboard/generation-drafts', {'workspacePath': target_workspace,
        'workspaceKind': 'notebook', 'sessionUpdatedAt': stamp, 'cache': cache})
    readback = client.api('/api/whiteboard/generation-drafts?' + target_query)['cache']
    for mapping in mappings:
        expected = cache['entries'][mapping['targetKey']]['values']
        actual = readback['entries'][mapping['targetKey']]['values']
        for field in ('prompt', 'duration', 'aspectRatio', 'referenceOrder', 'promptReferenceSequence'):
            if expected.get(field) != actual.get(field):
                raise ValueError('笔记草稿回读不一致：' + mapping['nodeId'] + '/' + field)
    native_active_before = target_cache.get('active')
    if readback.get('active') != native_active_before:
        raise ValueError('目标工作区操作栏状态出现并发变化；草稿已保存，请重新读取核对')
    receipt = bridge.read_json(receipt_file) if receipt_file.exists() else {'state': 'reused-existing-copy'}
    result = {'state': 'copied-and-verified', 'workspacePath': target_workspace, 'workspaceKind': 'notebook',
        'documentId': target_document_id, 'documentTitle': target_document['title'],
        'sourceWorkspacePath': source_workspace, 'sourceWorkspaceKind': source_kind,
        'sourceDocumentId': args.source_document_id, 'sourceBindingFile': str(Path(args.source_binding).resolve()),
        'notebookBindingFile': str(notebook_binding_file.resolve()), 'copyReceiptFile': str(receipt_file),
        'copyReceipt': receipt, 'attachmentVerificationFile': str(out / 'notebook-attachment-verification.json'),
        'attachmentCount': verification['uniqueFileCount'], 'draftCount': len(mappings), 'draftMappings': mappings,
        'sourcePreserved': True, 'jobsRetargeted': False, 'generationSubmitted': False,
        'preservedResults': preserved_results, 'finalPromptRedistributionPending': True,
        'archivedVideoHistoryRemaps': archived_remaps,
        'targetActiveWorkbenchPreserved': True, 'completedAt': bridge.now(), 'uiVerified': False}
    bridge.write_json(binding_file, result)
    summary = {k: v for k, v in result.items() if k not in ('copyReceipt', 'draftMappings', 'preservedResults')}
    summary['preservedResults'] = [{k: v for k, v in item.items() if k != 'originalGeneration'}
                                   for item in preserved_results]
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        raise SystemExit(1)
