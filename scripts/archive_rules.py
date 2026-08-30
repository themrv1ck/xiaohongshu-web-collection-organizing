"""Deterministic first-archive rules shared by classification paths."""

from typing import Any, Dict


UNCERTAIN_BOARD_NAME = '无法确定'
UNCERTAIN_REASON = 'uncertain_assignment_pending_user_reclassification'
UNCERTAIN_REVIEW_STATE = 'manual_reclassification_required'


def apply_uncertain_assignment(row: Dict[str, Any], *, protected: bool = False) -> Dict[str, Any]:
    """Route an explicitly unclassified pending note to the fixed review album."""
    if protected or str(row.get('target_board') or '').strip():
        return row
    result = dict(row)
    reasons = result.get('reason') or []
    if not isinstance(reasons, list):
        raise ValueError('classification reason must be an array')
    normalized_reasons = [str(value).strip() for value in reasons if str(value).strip()]
    if UNCERTAIN_REASON not in normalized_reasons:
        normalized_reasons.append(UNCERTAIN_REASON)
    result.update({
        'target_board': UNCERTAIN_BOARD_NAME,
        'confidence': 'low',
        'reason': normalized_reasons,
        'review_state': UNCERTAIN_REVIEW_STATE,
        'uncertain_assignment': True,
    })
    return result
