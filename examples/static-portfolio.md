---
name: "Static Portfolio"
timebox: "5m"
constraints:
  - "HTML and CSS only"
  - "No JavaScript frameworks"
  - "No build tools needed"
acceptance:
  - "cmd: test -f index.html"
  - "cmd: test -f style.css"
  - "Page has proper HTML5 structure"
---

# Static Portfolio Site

Build a clean, minimal personal portfolio website.

## Requirements

- `index.html` — single page with:
  - Navigation bar with links to sections
  - Hero section with name and tagline
  - About section with a short bio
  - Projects section with 3 placeholder project cards
  - Contact section with email link
  - Footer with copyright
- `style.css` — clean styling:
  - Responsive layout (works on mobile and desktop)
  - Modern color scheme
  - Clean typography
  - Card layout for projects
- Placeholder content is fine (lorem ipsum, etc.)
