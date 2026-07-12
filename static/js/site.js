window.YSL = window.YSL || {};

window.YSL.initSite = function initSite() {
  document.querySelectorAll(".reveal").forEach((item) => {
    item.classList.remove("show");
  });

  const items = document.querySelectorAll(".reveal");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduced) {
    items.forEach((item) => item.classList.add("show"));
  } else {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("show");
        io.unobserve(entry.target);
      });
    }, { threshold: .14 });

    items.forEach((item) => io.observe(item));
  }

  const menu = document.querySelector(".menu");
  const nav = document.querySelector(".nav nav");

  if (menu && nav) {
    menu.onclick = () => {
      const open = menu.getAttribute("aria-expanded") === "true";
      menu.setAttribute("aria-expanded", String(!open));
      nav.classList.toggle("open", !open);
      document.body.classList.toggle("locked", !open);
    };

    nav.querySelectorAll("a").forEach((link) => {
      link.onclick = () => {
        menu.setAttribute("aria-expanded", "false");
        nav.classList.remove("open");
        document.body.classList.remove("locked");
      };
    });
  }

  const year = document.getElementById("year");
  if (year) year.textContent = new Date().getFullYear();
};

window.YSL.initSite();
