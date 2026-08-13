// Drag a task card into another status column to move it, as an alternative to
// opening the card and changing the Status field. The edit modal is untouched
// and is still the only way to do this from the keyboard.
//
// Everything is delegated off document rather than bound per card: the board
// re-renders wholesale every 5s (poll) and after every write (oob swap), so
// listeners attached to individual cards would have to be rebound on each
// htmx:load and would leak the ones morphed away in between.
(function () {
  'use strict';

  var dragged = null; // .task-card currently being dragged
  var fromColumn = null; // .board-column it started in
  var hoverColumn = null; // .board-column highlighted right now
  var pending = 0; // in-flight status posts
  var lastDragEnd = 0;

  function board() {
    return document.getElementById('board-container');
  }

  // Only columns the server marked droppable: data-status is absent on the
  // by-user board (columns are people) and the archived board (read-only).
  function columnAt(node) {
    return node && node.closest ? node.closest('.board-column[data-status]') : null;
  }

  function cardAt(node) {
    return node && node.closest ? node.closest('.task-card[draggable="true"]') : null;
  }

  function setHover(col) {
    if (hoverColumn === col) return;
    if (hoverColumn) hoverColumn.classList.remove('drop-over');
    hoverColumn = col;
    if (hoverColumn) hoverColumn.classList.add('drop-over');
  }

  function reset() {
    if (dragged) dragged.classList.remove('is-dragging');
    setHover(null);
    dragged = null;
    fromColumn = null;
    document.body.classList.remove('dragging');
  }

  // Keeps a column's count badge and its "Nothing here." placeholder honest
  // after a card is moved in or out of it. The server refresh that follows
  // renders both anyway; this is just so they aren't visibly wrong in the
  // meantime.
  function recount(col) {
    if (!col) return;
    var badge = col.querySelector('.column-count');
    var cards = col.querySelectorAll('.task-card').length;
    if (badge) badge.textContent = cards;

    var placeholder = col.querySelector('.empty-column');
    if (cards && placeholder) placeholder.remove();
    if (!cards && !placeholder) {
      var p = document.createElement('p');
      p.className = 'empty-column';
      p.textContent = 'Nothing here.';
      col.appendChild(p);
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
  // end of the column. Appending was correct while columns were in insertion
  // order; now that they're sorted by due date, it would show every drop
  // landing at the bottom and then jumping once the refresh arrives.
  function insertSorted(card, column) {
    var rank = dueRank(card);
    var siblings = column.querySelectorAll('.task-card');
    for (var i = 0; i < siblings.length; i++) {
      if (dueRank(siblings[i]) > rank) {
        column.insertBefore(card, siblings[i]);
        return;
      }
    }
    column.appendChild(card);
  }

  // Move the card now and correct it from the server response, instead of
  // waiting on the round trip. Supabase is a few hundred ms away on this
  // deployment, and a card that hangs in the old column until it answers reads
  // as a drop that didn't take.
  function moveCard(card, toColumn, status) {
    var source = fromColumn;
    insertSorted(card, toColumn);
    card.dataset.status = status;
    // Snapshot first: classList is live, and removing while iterating it skips
    // entries.
    Array.prototype.slice.call(card.classList).forEach(function (cls) {
      if (cls.indexOf('status-') === 0) card.classList.remove(cls);
    });
    card.classList.add('status-' + status);
    recount(source);
    recount(toColumn);
  }

  function refreshBoard() {
    var b = board();
    if (!b) return;
    // Same URL and swap the 5s poll uses - it's rendered onto the element, so
    // there's no second copy of the query string to keep in sync.
    htmx.ajax('GET', b.getAttribute('hx-get'), { target: '#board-container', swap: 'morph' });
  }

  function postStatus(taskId, status) {
    var b = board();
    pending++;
    htmx
      .ajax('POST', '/tasks/' + encodeURIComponent(taskId) + '/status', {
        target: '#modal-root',
        swap: 'innerHTML',
        values: {
          status: status,
          active_workstream: b ? b.dataset.workstream : 'all',
          active_project: b ? b.dataset.project : 'all'
        }
      })
      .then(
        function () {
          pending--;
          // A refused move (not your task) comes back as a permission modal
          // rather than the board+sidebar refresh, which leaves the optimistic
          // move above on screen next to a message saying it didn't happen.
          // Pull the real board immediately instead of waiting for the poll.
          var modal = document.getElementById('modal-root');
          if (modal && modal.children.length) refreshBoard();
        },
        function () {
          pending--;
          refreshBoard();
        }
      );
  }

  document.addEventListener('dragstart', function (e) {
    var card = cardAt(e.target);
    if (!card) return;
    dragged = card;
    fromColumn = columnAt(card);
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = 'move';
      // Firefox won't start a drag at all unless some data is set.
      e.dataTransfer.setData('text/plain', card.dataset.taskId || '');
    }
    // Deferred a tick: the browser snapshots the drag image synchronously
    // after this handler returns, and restyling the card now would make the
    // ghost that follows the cursor the faded version too.
    setTimeout(function () {
      if (dragged !== card) return;
      card.classList.add('is-dragging');
      document.body.classList.add('dragging');
    }, 0);
  });

  document.addEventListener('dragover', function (e) {
    if (!dragged) return;
    var col = columnAt(e.target);
    // The column the card came from is left unhighlighted - dropping back into
    // it changes nothing - but the drop is still allowed there, so putting a
    // card back where it started doesn't flash a "not allowed" cursor.
    setHover(col === fromColumn ? null : col);
    if (!col) return;
    e.preventDefault(); // the drop only becomes legal once this is called
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
  });

  document.addEventListener('drop', function (e) {
    if (!dragged) return;
    var col = columnAt(e.target);
    if (!col) return;
    e.preventDefault();

    var card = dragged;
    var status = col.dataset.status;
    if (!card.dataset.taskId || card.dataset.status === status) {
      reset();
      return;
    }
    moveCard(card, col, status);
    reset();
    postStatus(card.dataset.taskId, status);
  });

  document.addEventListener('dragend', function () {
    lastDragEnd = Date.now();
    reset();
  });

  // Cards are <button>s wired to open the edit modal on click. Browsers
  // shouldn't fire a click after a drag, but a drag that ends as a near-zero
  // move can still land as one, and having the modal pop open every so often
  // on a failed drag would be worse than the drag not working. Capture phase,
  // so htmx's own listener on the button never sees the event.
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

  // The board polls every 5s and morphs itself in place. Landing mid-drag
  // would pull the card out from under the cursor and kill the drag; landing
  // between the drop and its response would render the pre-drop board and
  // undo the optimistic move for a beat. Skip those ticks - htmx:beforeRequest
  // is cancelable - and let the drop's own refresh be what updates the board.
  // Only the poll is affected: requests issued from here have document.body as
  // their source element, not #board-container.
  document.addEventListener('htmx:beforeRequest', function (e) {
    if (!dragged && !pending) return;
    var elt = e.detail && e.detail.elt;
    if (elt && elt.id === 'board-container') e.preventDefault();
  });
})();
