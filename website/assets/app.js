// ── Nav scroll shadow
const nav = document.getElementById("nav");
window.addEventListener("scroll", () => {
  nav.classList.toggle("nav--scrolled", window.scrollY > 16);
});

// ── Mobile nav burger
const burger = document.getElementById("nav-burger");
const navLinks = document.getElementById("nav-links");
burger.addEventListener("click", () => {
  navLinks.classList.toggle("open");
});
// Close on link click
navLinks.querySelectorAll("a").forEach(a => {
  a.addEventListener("click", () => navLinks.classList.remove("open"));
});

// ── Scroll-based fade-in
const observer = new IntersectionObserver((entries) => {
  entries.forEach(e => { if (e.isIntersecting) e.target.classList.add("visible"); });
}, { threshold: 0.12 });

document.querySelectorAll(
  ".feature-card, .how__step, .integration-card, .faq__item, .qs__step"
).forEach(el => {
  el.classList.add("fade-up");
  observer.observe(el);
});

// ── Active nav link highlight on scroll
const sections = document.querySelectorAll("section[id]");
const navAnchors = document.querySelectorAll(".nav__links a");
const highlightNav = () => {
  let current = "";
  sections.forEach(s => {
    if (window.scrollY >= s.offsetTop - 100) current = s.id;
  });
  navAnchors.forEach(a => {
    a.style.color = a.getAttribute("href") === "#" + current ? "var(--text)" : "";
  });
};
window.addEventListener("scroll", highlightNav, { passive: true });
