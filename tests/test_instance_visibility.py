from types import SimpleNamespace as NS

from atracsys_sync.instance_visibility import install, remove_instance
from atracsys_sync.marker_selection import visible_marker_choices


def test_remove_one_instance_does_not_hide_sibling_or_filter_recording():
    events=[]
    def widget(name):
        return NS(deleteLater=lambda:events.append(('delete',name)),
                  setVisible=lambda value:events.append((name,value)),
                  setText=lambda value:events.append((name,value)))
    first,second=widget('first'),widget('second')
    window=NS(_marker_cards={(301,1):first,(301,2):second},_dismissed_markers=set(),
              _dismissed_geometry_ids=set(),_focused_marker=(301,1),
              marker_layout=NS(removeWidget=lambda card:events.append(('remove',card))),
              _clear_focus=lambda:events.append(('clear_focus',True)),
              _refresh_marker_colors_and_instances=lambda:None,
              empty_marker_label=widget('empty'),marker_count_label=widget('count'),
              restore_markers_button=widget('restore'))
    # No recorder is supplied: hiding a card must never touch recording filters.
    remove_instance(window,(301,1))
    assert window._marker_cards=={(301,2):second}
    assert window._dismissed_markers=={(301,1)}
    assert not window._dismissed_geometry_ids
    assert ('count','1') in events
    remove_instance(window,(301,1))  # repeated click is harmless
    assert window._marker_cards=={(301,2):second}


def test_hidden_instance_stays_hidden_until_restored_and_sibling_is_selectable():
    class Window:
        def _sync_marker_cards(self,states):
            self.received=states
        _remove_marker=None
    install(Window)
    window=Window()
    window._dismissed_markers={(301,1)};window._dismissed_geometry_ids=set()
    window.restore_markers_button=NS(setVisible=lambda _:None)
    def state(identity,tracking):
        return NS(identity_key=(301,identity),identity_id=identity,visible=True,
                  age_ms=lambda:0,pose=NS(valid=True,geometry_id=301,tracking_id=tracking))
    states=(state(1,20),state(2,21))
    window._sync_marker_cards(states)
    assert window.received==(states[1],)
    window.status_store=NS(get=lambda:NS(connected=True))
    window.marker_registry=NS(states=lambda:states)
    assert list(visible_marker_choices(window))==['301:21']
    window._dismissed_markers.clear()
    window._sync_marker_cards(states)
    assert window.received==states
    assert list(visible_marker_choices(window))==['301:20','301:21']
