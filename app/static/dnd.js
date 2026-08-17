// Drag a task card into another group to move it, as an alternative to opening
// the card and changing the field by hand. The edit modal is untouched and is
// still the only way to do any of this from the keyboard.
//
// What a drop writes is declared by the element it lands on rather than known
// here, which is what lets one file serve pages that group tasks differently:
//
//   data-drop-status    set the status    (board columns, team, profile)
//   data-drop-assignee  set the assignee  (by-user columns, team columns)
//   data-drop-due       set the due date  (my day's deadline sections)
//
// Several can sit on one element - a column on the team page is both a status and
// a person, so a drop there does both in one write. An empty value is meaningful:
// data-drop-assignee="" unassigns, data-drop-due="" clears the deadline.
//
// The enclosing [data-dnd-scope] element says which page this is, because those
// pages don't share a refresh fragment and three of them can't be told apart
// from a board server-side (see app/fragments.py).
//
// Everything is delegated off document rather than bound per card: these views
// re-render wholesale on a poll and after every write (oob swap), so listeners
// attached to individual cards would have to be rebound on each htmx:load and
// would leak the ones morphed away in between.
(function () {
  'use strict';

  var ZONE = '[data-drop-status],[data-drop-assignee],[data-drop-due]';

  var dragged = null; // .task-card currently being dragged
  var fromZone = null; // the drop zone it started in
  var origin = null; // where it sat before the optimistic move, to put it back
  var hoverZone = null; // the zone highlighted right now
  var pending = 0; // in-flight moves
  var lastDragEnd = 0;

  // Only zones the server marked droppable: none of these attributes is rendered
  // on the by-user board's placeholder column or anywhere on the archived board,
  // which is a history view rather than somewhere to reshuffle work.
  function zoneAt(node) {
    return node && node.closest ? node.closest(ZONE) : null;
  }

  function cardAt(node) {
    return node && node.closest ? node.closest('.task-card[draggable="true"]') : null;
  }

  // Where a zone keeps its cards. Board columns hold them directly; a my-day
  // section wraps them in a grid below its header, so the cards go one level in
  // from the element carrying the data-drop-* attributes.
  function cardsOf(zone) {
    return zone.querySelector('[data-drop-cards]') || zone;
  }

  // Which fields a drop into this zone would actually change. Empty means the
  // drop is a no-op, and is what keeps an idle drag from writing a row and
  // logging an activity event for nothing. The server narrows the same way, so
  // this is the fast path rather than the guarantee.
  function changedFields(card, zone) {
    // Already in this zone: nothing about it can be new. Checked before the
    // field-by-field comparison because a deadline section writes one fixed date
    // for a range of them ("Rest of this week" -> the far end of the window), so
    // a card sitting in it can differ from that date while already being in the
    // right place - and re-dropping it there should not shunt its deadline.
    if (cardsOf(zone).contains(card)) return [];

    var fields = [];
    function consider(attr, field, current) {
      if (zone.hasAttribute(attr) && zone.getAttribute(attr) !== current) fields.push(field);
    }
    consider('data-drop-status', 'status', card.dataset.status || '');
    consider('data-drop-assignee', 'assignee_id', card.dataset.assignee || '');
    consider('data-drop-due', 'due_date', card.dataset.due || '');
    return fields;
  }

  function setHover(zone) {
    if (hoverZone === zone) return;
    if (hoverZone) hoverZone.classList.remove('drop-over');
    hoverZone = zone;
    if (hoverZone) hoverZone.classList.add('drop-over');
  }

  function reset() {
    if (dragged) dragged.classList.remove('is-dragging');
    setHover(null);
    dragged = null;
    fromZone = null;
    origin = null;
    document.body.classList.remove('dragging');
  }

  function setStatus(card, status) {
    card.dataset.status = status;
    // Snapshot first: classList is live, and removing while iterating it skips
    // entries.
    Array.prototype.slice.call(card.classList).forEach(function (cls) {
      if (cls.indexOf('status-') === 0) card.classList.remove(cls);
    });
    if (status) card.classList.add('status-' + status);
  }

  // Keeps a zone's count badge, its "Nothing here." placeholder and (on my day)
  // its hidden-when-empty state honest after a card moves in or out. The server
  // refresh that follows renders all three anyway; this is just so they aren't
  // visibly wrong in the meantime.
  function recount(zone) {
    if (!zone) return;
    var cards = cardsOf(zone).querySelectorAll('.task-card').length;

    var badge = zone.querySelector('.column-count');
    if (badge) badge.textContent = cards;

    // My day hides its empty deadline sections at rest and reveals the droppable
    // ones for the length of a drag - see the my-day-section rules in styles.css.
    if (zone.classList.contains('my-day-section')) zone.classList.toggle('is-empty', cards === 0);

    var placeholder = zone.querySelector('.empty-column');
    if (cards && placeholder) placeholder.remove();
    if (!cards && !placeholder && zone.classList.contains('board-column')) {
      var p = document.createElement('p');
      p.className = 'empty-column';
      p.textContent = 'Nothing here.';
      zone.appendChild(p);
    }
  }

  // Mirrors due_date_sort_key in app/view_helpers.py: soonest due date first,
  // undated last. Dates are ISO strings, so the prefix makes both groups one
  // comparable string.
  function dueRank(card) {
    var due = card.dataset.due;
    return due ? '0' + due : '1';
  }

  // Drop the card into the slot the server would put it in, rather than at the
  // end of the group. Appending was correct while columns were in insertion
  // order; now that they're sorted by due date, it would show every drop landing
  // at the bottom and then jumping once the refresh arrives.
  function insertSorted(card, into) {
    var rank = dueRank(card);
    var siblings = into.querySelectorAll('.task-card');
    for (var i = 0; i < siblings.length; i++) {
      if (dueRank(siblings[i]) > rank) {
        into.insertBefore(card, siblings[i]);
        return;
      }
    }
    into.appendChild(card);
  }

  // Move the card now and correct it from the server response, instead of waiting
  // on the round trip. Supabase is a few hundred ms away on this deployment, and
  // a card that hangs in the old group until it answers reads as a drop that
  // didn't take.
  function moveCard(card, zone, fields) {
    var from = fromZone;
    var into = cardsOf(zone);
    // Sorted into place where the order is by due date - which is how every
    // status column reads - and appended where this drop is what changes that
    // date, since the card still shows the old one until the refresh lands.
    if (fields.indexOf('due_date') === -1) insertSorted(card, into);
    else into.appendChild(card);

    // The card's own data-* are what the NEXT drag compares against, so they move
    // with it rather than waiting for the refresh. Only the visible "Due Aug 20"
    // line lags, for the length of one round trip - rewriting that here would mean
    // a second copy of format_due_date (app/view_helpers.py) in JS.
    if (zone.hasAttribute('data-drop-status')) setStatus(card, zone.getAttribute('data-drop-status'));
    if (zone.hasAttribute('data-drop-assignee')) card.dataset.assignee = zone.getAttribute('data-drop-assignee');
    if (zone.hasAttribute('data-drop-due')) card.dataset.due = zone.getAttribute('data-drop-due');
    recount(from);
    recount(zone);
  }

  // Put the card back where the drag started. Used when the move is refused or
  // never lands, instead of re-fetching: the four pages this runs on don't all
  // have a fragment to re-fetch, and an undo is instant where a round trip is
  // not. The board and team page also poll, so anything this gets wrong (a write
  // that landed but whose response didn't) is corrected within a few seconds.
  function restore(card, snapshot, zone) {
    if (!snapshot || !snapshot.parent || !card.isConnected || !snapshot.parent.isConnected) return;
    if (snapshot.next && snapshot.next.parentNode === snapshot.parent) {
      snapshot.parent.insertBefore(card, snapshot.next);
    } else {
      snapshot.parent.appendChild(card);
    }
    setStatus(card, snapshot.status);
    card.dataset.assignee = snapshot.assignee;
    card.dataset.due = snapshot.due;
    recount(zone);
    recount(snapshot.zone);
  }

  function postMove(card, zone, fields, snapshot) {
    var root = zone.closest('[data-dnd-scope]');
    var values = {
      // Which fields this drop writes, named separately from their values because
      // "" is itself a value for two of them (unassign, clear the deadline) and is
      // otherwise indistinguishable from a field that was never sent.
      fields: fields.join(','),
      scope: root ? root.dataset.dndScope : 'board',
      scope_user: (root && root.dataset.dndUser) || '',
      active_workstream: (root && root.dataset.workstream) || 'all',
      active_project: (root && root.dataset.project) || 'all'
    };
    if (zone.hasAttribute('data-drop-status')) values.status = zone.getAttribute('data-drop-status');
    if (zone.hasAttribute('data-drop-assignee')) values.assignee_id = zone.getAttribute('data-drop-assignee');
    if (zone.hasAttribute('data-drop-due')) values.due_date = zone.getAttribute('data-drop-due');

    pending++;
    htmx
      .ajax('POST', '/tasks/' + encodeURIComponent(card.dataset.taskId) + '/move', {
        target: '#modal-root',
        swap: 'innerHTML',
        values: values
      })
      .then(
        function () {
          pending--;
          // A refused move (not your task) comes back as a permission modal
          // rather than the oob refresh, which would otherwise leave the
          // optimistic move on screen next to a message saying it didn't happen.
          var modal = document.getElementById('modal-root');
          if (modal && modal.children.length) restore(card, snapshot, zone);
        },
        function () {
          pending--;
          restore(card, snapshot, zone);
        }
      );
  }

  document.addEventListener('dragstart', function (e) {
    var card = cardAt(e.target);
    if (!card) return;
    dragged = card;
    fromZone = zoneAt(card);
    // Captured before anything moves: a refused drop puts the card back here.
    origin = {
      parent: card.parentNode,
      next: card.nextElementSibling,
      status: card.dataset.status || '',
      assignee: card.dataset.assignee || '',
      due: card.dataset.due || '',
      zone: fromZone
    };
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = 'move';
      // Cleared first because the team and profile pages' cards are anchors, and
      // the browser seeds the drag with their href - which would otherwise be
      // offered as a link to whatever the card was dropped on. Then the task id,
      // because Firefox won't start a drag at all unless some data is set.
      e.dataTransfer.clearData();
      e.dataTransfer.setData('text/plain', card.dataset.taskId || '');
    }
    // Deferred a tick: the browser snapshots the drag image synchronously after
    // this handler returns, and restyling the card now would make the ghost that
    // follows the cursor the faded version too.
    setTimeout(function () {
      if (dragged !== card) return;
      card.classList.add('is-dragging');
      document.body.classList.add('dragging');
    }, 0);
  });

  document.addEventListener('dragover', function (e) {
    if (!dragged) return;

    // Dragging over a collapsed teammate opens their board. Without this,
    // reassigning on the team page would mean expanding both people first and
    // hoping they were on screen together.
    var summary = e.target.closest ? e.target.closest('.team-summary') : null;
    if (summary && summary.parentNode && !summary.parentNode.open) summary.parentNode.open = true;

    var zone = zoneAt(e.target);
    // A zone the drop wouldn't change is left unhighlighted - dropping a card
    // back where it came from changes nothing - but the drop is still allowed
    // there, so putting it back doesn't flash a "not allowed" cursor.
    setHover(zone && changedFields(dragged, zone).length ? zone : null);
    if (!zone) return;
    e.preventDefault(); // the drop only becomes legal once this is called
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
  });

  document.addEventListener('drop', function (e) {
    if (!dragged) return;
    var zone = zoneAt(e.target);
    if (!zone) return;
    // Also what stops an anchor card's href being followed on release.
    e.preventDefault();

    var card = dragged;
    var snapshot = origin;
    var fields = card.dataset.taskId ? changedFields(card, zone) : [];
    if (!fields.length) {
      reset();
      return;
    }
    moveCard(card, zone, fields);
    reset();
    postMove(card, zone, fields, snapshot);
  });

  document.addEventListener('dragend', function () {
    lastDragEnd = Date.now();
    reset();
  });

  // Cards are buttons wired to open the edit modal on click, or - on the team and
  // profile pages - links to the board the task lives on. Browsers shouldn't fire
  // a click after a drag, but a drag that ends as a near-zero move can still land
  // as one, and having the page navigate away every so often on a failed drag
  // would be worse than the drag not working. Capture phase, so neither htmx's
  // listener nor the browser's own link handling ever sees the event.
  document.addEventListener(
    'click',
    function (e) {
      if (Date.now() - lastDragEnd > 150) return;
      if (!cardAt(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
    },
    true
  );

  // These views poll and morph themselves in place. Landing mid-drag would pull
  // the card out from under the cursor and kill the drag; landing between the drop
  // and its response would render the pre-drop state and undo the optimistic move
  // for a beat. Skip those ticks - htmx:beforeRequest is cancelable - and let the
  // drop's own oob refresh be what updates the page.
  //
  // Matched by "would this redraw a drop zone" rather than by id, so the board
  // (5s) and the team panel (10s) are both covered while the sidebar poll, which
  // draws nothing being dragged, is left alone. Requests issued from this file
  // have document.body as their source element and so never match.
  document.addEventListener('htmx:beforeRequest', function (e) {
    if (!dragged && !pending) return;
    var elt = e.detail && e.detail.elt;
    if (!elt || elt === document.body || !elt.querySelector) return;
    if (elt.querySelector(ZONE)) e.preventDefault();
  });
})();
