const test = require('node:test')
const assert = require('node:assert/strict')
const { compareVersions, validateRelease, installerArguments } = require('../electron/updater.cjs')
const release = { version:'0.7.9', filename:'galaxy-ai-Setup-0.7.9.exe', download_path:'/api/v1/updates/packages/galaxy-ai-Setup-0.7.9.exe', size:123, sha256:'a'.repeat(64), notes:'更新说明', published_at:'2026-11-18T00:00:00Z' }
test('numeric versions handle 0.7.10 and never auto-downgrade', () => {
  assert.equal(compareVersions('0.7.10','0.7.9'),1)
  assert.equal(compareVersions('0.7.9','0.7.9'),0)
  assert.equal(compareVersions('0.7.8','0.7.9'),-1)
  assert.throws(() => compareVersions('latest','0.7.9'))
})
test('only the version-named package at the fixed server path is accepted', () => {
  assert.deepEqual(validateRelease(release),release)
  for(const field of [{filename:'../../other.exe'}, {download_path:'https://evil.invalid/file.exe'}, {sha256:'bad'}, {size:0}, {size:513*1024*1024}, {version:'1.2.3/evil'}]) {
    assert.throws(() => validateRelease({...release,...field}))
  }
})
test('NSIS receives a single unquoted last directory argument, no shell', () => {
  assert.deepEqual(installerArguments('C:\\Program Files\\GalaxyAI'),['/S','--updated','--force-run','/D=C:\\Program Files\\GalaxyAI'])
  assert.throws(() => installerArguments('relative/path'))
  assert.throws(() => installerArguments('C:\\bad"path'))
})
