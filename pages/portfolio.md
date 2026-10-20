--- 
layout: page
title : Projects 
permalink: /projects/
feature-img: "assets/img/pexels/projects.jpg"
---

<ul class="project-cards">
  {% for p in site.data.projects %}
  {% if p.public == false %}{% assign href = p.interim_url %}{% else %}{% assign href = p.url | relative_url %}{% endif %}
  <li class="project-card">
    <a href="{{ href }}" class="project-card-image"><img src="{{ p.image | relative_url }}" alt="{{ p.alt }}"{% if p.image_position %} style="object-position: {{ p.image_position }}"{% endif %}></a>
    <div>
      <h2><a href="{{ href }}">{{ p.title }}</a></h2>
      <p class="project-card-meta">{{ p.year }}, {{ p.partners }}</p>
      <p>{{ p.summary }}</p>
    </div>
  </li>
  {% endfor %}
</ul>
