document.addEventListener('DOMContentLoaded', () => {
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('sidebarToggle');

  if (sidebar && toggle) {
    let backdrop = document.querySelector('.mobile-sidebar-backdrop');
    if (!backdrop) {
      backdrop = document.createElement('div');
      backdrop.className = 'mobile-sidebar-backdrop';
      document.body.appendChild(backdrop);
    }

    const syncSidebarState = () => {
      const open = sidebar.classList.contains('show');
      backdrop.classList.toggle('show', open);
      document.body.classList.toggle('sidebar-open', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    };

    toggle.setAttribute('aria-controls', 'sidebar');
    toggle.setAttribute('aria-expanded', sidebar.classList.contains('show') ? 'true' : 'false');

    toggle.addEventListener('click', () => {
      window.setTimeout(syncSidebarState, 0);
    });

    backdrop.addEventListener('click', () => {
      sidebar.classList.remove('show');
      syncSidebarState();
    });

    sidebar.querySelectorAll('a.nav-item').forEach((link) => {
      link.addEventListener('click', () => {
        if (window.matchMedia('(max-width: 992px)').matches) {
          sidebar.classList.remove('show');
          syncSidebarState();
        }
      });
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && sidebar.classList.contains('show')) {
        sidebar.classList.remove('show');
        syncSidebarState();
      }
    });
  }

  document.querySelectorAll('.table-responsive').forEach((container) => {
    const table = container.querySelector(':scope > table');
    if (!table) return;

    if (
      table.classList.contains('calendar-table') ||
      table.dataset.mobile === 'scroll'
    ) {
      container.classList.add('mobile-scroll-container');
      return;
    }

    const headers = Array.from(table.querySelectorAll('thead th')).map(
      (cell) => cell.textContent.trim()
    );
    if (!headers.length) {
      container.classList.add('mobile-scroll-container');
      return;
    }

    table.classList.add('mobile-card-table');
    container.classList.add('mobile-card-container');

    table.querySelectorAll('tbody tr').forEach((row) => {
      const cells = Array.from(row.children).filter(
        (cell) => cell.tagName === 'TD'
      );

      cells.forEach((cell, index) => {
        if (cell.hasAttribute('colspan')) {
          cell.dataset.emptyRow = '1';
          return;
        }
        cell.dataset.label = headers[index] || '';
      });
    });
  });
});
