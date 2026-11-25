"""Browser regression checks for built-in themes and file controls."""
import re

from playwright.sync_api import expect


def check_theme_before_body(browser, state, base):
    """Render the HTTP response through its head, before the application bundle arrives."""
    for preference, system, wanted, local_skin in [
        ('light', 'dark', 'light', 'pixel'), ('dark', 'light', 'dark', None),
        ('auto', 'light', 'light', None),
    ]:
        context = browser.new_context(storage_state=state, color_scheme=system)
        context.add_init_script(f"localStorage.setItem('tsf_theme','{preference}');")
        if local_skin:
            context.add_init_script(f"localStorage.setItem('tsf_style','{local_skin}');")
        def head_only(route):
            response = route.fetch()
            head, end, _ = response.text().partition('</head>')
            assert end
            route.fulfill(content_type='text/html; charset=utf-8', body=head + end + '<body></body></html>')
        for path in ('/', '/login'):
            page = context.new_page()
            page.route(base + path, head_only)
            page.goto(base + path)
            expect(page.locator('html')).to_have_attribute('data-theme', wanted)
            expect(page.locator('html')).to_have_attribute('data-style', local_skin or 'apple')
            assert page.locator('html').evaluate('el => getComputedStyle(el).colorScheme') == wanted
            page.emulate_media(color_scheme='dark' if system == 'light' else 'light')
            expect(page.locator('html')).to_have_attribute('data-theme', 'dark' if preference == 'auto' else wanted)
            page.close()
        context.close()


def text_contrast(locator):
    """Measure text on its nearest opaque surface, including small-label opacity."""
    foreground, background, opacity = locator.evaluate("""el => {
      const text = getComputedStyle(el);
      let surface = el;
      while (surface && getComputedStyle(surface).backgroundColor === 'rgba(0, 0, 0, 0)')
        surface = surface.parentElement;
      return [text.color, getComputedStyle(surface).backgroundColor, Number(text.opacity)];
    }""")
    def channels(css):
        assert css.startswith('rgb('), css
        return [int(n) / 255 for n in re.findall(r'\d+', css)]
    def luminance(rgb):
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    bg = channels(background)
    fg = [a * opacity + b * (1 - opacity) for a, b in zip(channels(foreground), bg)]
    dark, light = sorted([luminance(fg), luminance(bg)])
    return (light + .05) / (dark + .05)


def check_theme_controls(page):
    """Start on the logged-in library page with a desktop viewport."""
    page.locator('#gear').click()
    # Changing a built-in skin also persists the device default. Exercise the UI
    # locally in this browser while leaving the shared default alone.
    def save_style(route):
        if route.request.method == 'POST':
            route.fulfill(json={'ok': True})
        else:
            route.continue_()
    page.route('**/api/ui/style', save_style)
    results = {}
    for theme in ('light', 'dark'):
        for _ in range(3):
            if page.locator('html').get_attribute('data-theme') == theme:
                break
            page.locator('#theme').click()
        expect(page.locator('html')).to_have_attribute('data-theme', theme)
        for skin in ('default', 'apple', 'claude', 'pixel'):
            page.locator('#st-skin').click()
            choice = page.locator(f'#style-pick button[data-style="{skin}"]')
            choice.click()
            matched = choice.evaluate("""btn => {
              const root = getComputedStyle(document.documentElement);
              const ctx = document.createElement('canvas').getContext('2d');
              const normalize = color => { ctx.fillStyle = color; return ctx.fillStyle; };
              const chips = [...btn.querySelectorAll('.sw i')];
              return chips.length === 2 && chips.every((chip, i) =>
                normalize(getComputedStyle(chip).backgroundColor) ===
                normalize(root.getPropertyValue(['--bg', '--accent'][i]).trim()));
            }""")
            assert matched, (skin, theme, 'preview')
            page.locator('#st-lang').click()
            page.evaluate("""async () => { await Promise.allSettled(document.getAnimations()
                .filter(a => a instanceof CSSTransition).map(a => a.finished)); }""")
            ratios = {selector: text_contrast(page.locator(selector).first) for selector in
                      ('#st-lang', '#lang-grid button.on', '#lang-grid button.on .code',
                       '#lang-grid button:not(.on) .code')}
            page.locator('#st-brand').click()
            ratios['brand-preview'] = text_contrast(page.locator('.brandmeta span'))
            dimensions = page.locator('#tab-brand .upbtn, #tab-brand .brandreset, #tab-brand .brandrow button').evaluate_all(
                """buttons => buttons.map(el => {
                  const style = getComputedStyle(el); return [style.fontSize, style.borderRadius];
                })""")
            assert len(dimensions) == 3 and all(d == dimensions[0] for d in dimensions), dimensions
            page.locator('#st-archive').click()
            field = page.locator('#dest-dir')
            expect(field).to_be_visible()
            field.focus()
            page.keyboard.press('Tab')
            page.keyboard.press('Shift+Tab')
            expect(field).to_be_focused()
            assert field.evaluate("el => getComputedStyle(el).outlineStyle !== 'none'")
            for name, control in [('path', field), ('jump', page.locator('.file-jump button')),
                                  ('breadcrumb', page.locator('#crumb a').first)]:
                ratios[name] = text_contrast(control)
            assert min(ratios.values()) >= 4.5, (theme, skin, ratios)
            results[f'{theme}/{skin}'] = {k: round(v, 2) for k, v in ratios.items()}
    return results
