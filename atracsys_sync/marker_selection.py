"""Visible live identities for free analysis; no inferred instance ordering."""
def visible_marker_choices(window):
    if not window.status_store.get().connected:
        return {}
    states = window.marker_registry.states()
    states = states.values() if isinstance(states, dict) else states
    dismissed = getattr(window, '_dismissed_geometry_ids', set())
    dismissed_instances = getattr(window, '_dismissed_markers', set())
    result = {}
    for state in states:
        pose = state.pose
        if (not state.visible or state.age_ms() > 500 or not pose.valid
                or pose.geometry_id in dismissed
                or getattr(state, 'identity_key', (pose.geometry_id, state.identity_id)) in dismissed_instances):
            continue
        key = f'{pose.geometry_id}:{pose.tracking_id}'
        result[key] = f'Geometry {pose.geometry_id} · Instance {state.identity_id} · Tracking {pose.tracking_id}'
    return result
