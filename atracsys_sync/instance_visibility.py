"""Hide one live identity without excluding its whole geometry from recording."""


def remove_instance(window, key):
    card = window._marker_cards.pop(key, None)
    if card is None:
        return
    window._dismissed_markers.add(key)
    window.marker_layout.removeWidget(card)
    card.deleteLater()
    if window._focused_marker == key:
        window._clear_focus()
    window._refresh_marker_colors_and_instances()
    window.empty_marker_label.setVisible(not window._marker_cards)
    window.marker_count_label.setText(str(len(window._marker_cards)))
    window.restore_markers_button.setVisible(True)


def install(window_class):
    original_sync = window_class._sync_marker_cards

    def sync_instances(window, states):
        visible_states = tuple(state for state in states
                               if state.identity_key not in window._dismissed_markers)
        result = original_sync(window, visible_states)
        window.restore_markers_button.setVisible(bool(window._dismissed_markers or window._dismissed_geometry_ids))
        return result

    window_class._sync_marker_cards = sync_instances
    window_class._remove_marker = remove_instance
