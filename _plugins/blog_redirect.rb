# The blog moved from /Blog/ to /blog/, where its later pages already were (/blog/page2/).
# This writes a redirect at the old address, in production builds only: macOS ignores case,
# so a local build would write it over /blog/index.html and /blog/ would redirect to itself.
module BlogRedirect
  class Generator < Jekyll::Generator
    safe true

    def generate(site)
      return unless Jekyll.env == "production"

      target = "#{site.config['baseurl']}/blog/"
      page = Jekyll::PageWithoutAFile.new(site, site.source, "Blog", "index.html")
      page.data["layout"] = nil
      page.data["sitemap"] = false
      page.content = <<~HTML
        <!doctype html>
        <html lang="en">
        <head>
        <meta charset="utf-8">
        <title>Blog</title>
        <meta name="robots" content="noindex">
        <link rel="canonical" href="#{site.config['url']}#{target}">
        <meta http-equiv="refresh" content="0; url=#{target}">
        </head>
        <body><a href="#{target}">The blog has moved to /blog/</a></body>
        </html>
      HTML
      site.pages << page
    end
  end
end
