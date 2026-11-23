// Private browser/parent bridge. The original page SDK still submits the verification.
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function createInteractiveSlider() {
  const challenges = new Map();

  async function observe(response) {
    const url = new URL(response.url());
    if (url.hostname !== 'passport.youzan.com' || response.request().method() !== 'GET') return;
    if (!['/api/captcha/get-behavior-captcha-token-v2.json',
      '/api/captcha/get-behavior-captcha-data.json'].includes(url.pathname)) return;
    const body = await response.json();
    if (body.code !== 0) return;
    const token = url.pathname.endsWith('get-behavior-captcha-token-v2.json')
      ? body.data?.token : url.searchParams.get('token');
    if (typeof token !== 'string' || !token) return;
    const state = challenges.get(token) || {};
    if (url.pathname.endsWith('get-behavior-captcha-token-v2.json')) state.randomString = body.data?.randomStr;
    else state.challenge = body.data?.captchaObtainInfoResult;
    challenges.set(token, state);
    while (challenges.size > 8) challenges.delete(challenges.keys().next().value);
  }

  async function perform({page, context, verification}) {
    const slider = page.locator('#slideBlockRef:visible').first();
    await slider.waitFor({state: 'visible', timeout: 10000});
    const puzzle = slider.locator('xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " content ")][1]');
    const bigImage = page.locator('#bigImg:visible').first();
    const currentImage = await bigImage.evaluate(async img => { await img.decode(); return img.currentSrc || img.src; });
    let state;
    for (let attempt = 0; attempt < 50; attempt++) {
      state = [...challenges.values()].find(item => item.randomString && item.challenge?.bigUrl &&
        new URL(item.challenge.bigUrl).href === currentImage);
      if (state) break;
      await pause(100);
    }
    if (!state) throw new Error('slider_challenge_context_unavailable');
    const geometry = await slider.evaluate(handle => {
      const component = handle.closest('.content');
      if (!component) throw new Error('widget_bounds_unavailable');
      const box = component.getBoundingClientRect(), knob = handle.getBoundingClientRect();
      return {width: box.width, start_x: Math.round(knob.x + knob.width / 2 - box.x),
        start_y: Math.round(knob.y + knob.height / 2 - box.y)};
    });
    const [background, piece] = await Promise.all([
      context.request.get(state.challenge.bigUrl), context.request.get(state.challenge.smallUrl)]);
    if (!background.ok() || !piece.ok()) throw new Error('slider_image_fetch_failed');
    const image = await puzzle.screenshot({type: 'jpeg', quality: 75, timeout: 5000});
    const action = await verification('slider', {image: image.toString('base64'), encoder_input: {
      background: (await background.body()).toString('base64'),
      piece: (await piece.body()).toString('base64'), raw_cy: state.challenge.cy,
      display_width: geometry.width, start_x: geometry.start_x, start_y: geometry.start_y,
      random_string: state.randomString,
    }});
    const {points, release_delay_ms = 0} = action;
    const handle = await slider.boundingBox();
    if (!handle || !Array.isArray(points) || points.length < 2 || points.length > 512 ||
        !Number.isInteger(release_delay_ms) || release_delay_ms < 0 || release_delay_ms > 1000) return false;
    let previous = 0;
    for (const point of points) {
      if (!['x', 'y', 'ms'].every(k => Number.isInteger(point[k])) ||
          Math.abs(point.x) > 500 || Math.abs(point.y) > 400 || point.ms < previous || point.ms > 10000) return false;
      previous = point.ms;
    }
    const check = page.waitForResponse(response =>
      new URL(response.url()).origin === 'https://passport.youzan.com' &&
      new URL(response.url()).pathname === '/api/captcha/check-behavior-captcha-data.json' &&
      response.request().method() === 'POST', {timeout: 22000});
    check.catch(() => {});
    const x = handle.x + handle.width / 2, y = handle.y + handle.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    const started = performance.now();
    try {
      for (const point of points) {
        const delay = point.ms - (performance.now() - started);
        if (delay > 0) await pause(delay);
        await page.mouse.move(x + point.x, y + point.y);
      }
      if (release_delay_ms) await pause(release_delay_ms);
    } finally { await page.mouse.up(); }
    return (await (await check).json()).data?.success === true;
  }

  return {observe, perform};
}

module.exports = {createInteractiveSlider};
