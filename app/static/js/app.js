document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.getElementById('sidebarToggle');
  const sidebar = document.getElementById('sidebar');
  if (toggle && sidebar) toggle.addEventListener('click', () => sidebar.classList.toggle('show'));
  document.querySelectorAll('[data-add-row]').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = document.querySelector(btn.dataset.addRow);
      const tpl = document.querySelector(btn.dataset.template);
      if (target && tpl) target.insertAdjacentHTML('beforeend', tpl.innerHTML);
    });
  });
});
