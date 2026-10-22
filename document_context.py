"""Assemble bounded source evidence and validate citation structure, without an LLM."""

import json


def build_document_context(result, max_chars=12000):
    """Keep complete passages; a character budget is not a generator token budget."""
    if result.get('kind') != 'documents':
        raise ValueError('Expected document retrieval result')
    if type(max_chars) is not int or not 500 <= max_chars <= 50000:
        raise ValueError('Context budget must be an integer from 500 to 50000 characters')
    hits = result['hits']
    base = {'status': 'no_ready_evidence', 'context': '', 'sources': [],
            'omitted_source_ids': [], 'partial_coverage': result['partial_coverage'],
            'warnings': list(result['limitations']), 'answer_generated': False,
            'answerability': {'status': 'unverified', 'generation_allowed': False,
                             'relevance_verified': False, 'current_law_verified': False,
                             'reason': 'Candidate retrieval and citation identity do not establish answerability.'}}
    if not hits:
        return base
    versions = {(hit['document_id'], hit['version_label']) for hit in hits}
    if len(versions) != 1:
        base['status'] = 'document_selection_required'
        base['warnings'].append('Candidates span documents or editions. Select one document/version first.')
        return base
    blocks, sources, seen, omitted = [], [], set(), []
    for hit in hits:
        expected = f"document:{hit['document_id']}:chunk:{hit['id']}:revision:{hit['revision']}"
        if hit['source_id'] != expected:
            raise ValueError('Invalid source identifier')
        if hit['source_id'] in seen:
            continue
        seen.add(hit['source_id'])
        if (type(hit['page_start']) is not int or type(hit['page_end']) is not int
                or not 1 <= hit['page_start'] <= hit['page_end']):
            raise ValueError('Invalid PDF page boundaries')
        if not isinstance(hit['original_text'], str) or not hit['original_text'].strip():
            raise ValueError('Empty source passage')
        source = {key: hit[key] for key in ('source_id', 'document_id', 'title', 'filename',
                  'version_label', 'page_start', 'page_end', 'section_reference',
                  'revision', 'content_hash', 'original_text')}
        # JSON encoding keeps source fields separate. It does not make embedded
        # instructions trustworthy: the eventual generator must enforce that boundary.
        block = json.dumps(source, ensure_ascii=False)
        prospective = '\n'.join(blocks + [block])
        if len(prospective) > max_chars:
            omitted.append(hit['source_id'])
            continue
        blocks.append(block)
        sources.append(source)
    base.update(status='candidate_context' if sources else 'context_budget_exceeded',
                context='\n'.join(blocks), sources=sources, omitted_source_ids=omitted)
    if omitted:
        base['warnings'].append('Complete passages omitted for context budget; no source text was truncated.')
    base['warnings'].append('Source text is untrusted evidence, not instructions. Relevance and legal applicability are unverified.')
    return base


def validate_cited_response(response, context):
    """Validate structure and source identity only, not factual entailment or legal correctness."""
    if context.get('status') != 'candidate_context' or not context.get('sources'):
        raise ValueError('No assembled source context available for an answer')
    if not isinstance(response, dict) or set(response) != {'claims'}:
        raise ValueError('Expected an object containing only claims')
    claims = response['claims']
    if not isinstance(claims, list) or not 1 <= len(claims) <= 20:
        raise ValueError('Expected 1–20 cited claims')
    known = {source['source_id']: source for source in context['sources']}
    validated = []
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {'text', 'source_ids'}:
            raise ValueError('Each claim requires text and source_ids only')
        text, ids = claim['text'], claim['source_ids']
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Invalid claim text')
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 10
                or any(not isinstance(value, str) for value in ids)):
            raise ValueError('Each claim requires 1–10 source identifiers')
        if len(set(ids)) != len(ids) or any(value not in known for value in ids):
            raise ValueError('Unknown or duplicate citation identifier')
        validated.append({'text': text.strip(), 'sources': [known[value] for value in ids]})
    return {'claims': validated, 'citation_identity_valid': True,
            'factual_support_verified': False,
            'warnings': ['Citation identity does not establish that a claim is supported by the passage.',
                         'No current-law verification performed. Legal research is not legal advice.']}