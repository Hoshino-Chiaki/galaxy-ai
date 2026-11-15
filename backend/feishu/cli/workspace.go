// All upstream file operations are bound to this employee's workspace.
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/larksuite/cli/extension/fileio"
)

type workspaceFiles struct { root *os.Root; directory string }
type savedFile int64
func (s savedFile) Size() int64 {return int64(s)}
func newWorkspaceFiles(directory string) (*workspaceFiles,error) {
	if directory=="" || !filepath.IsAbs(directory) {return nil,fs.ErrPermission}
	r,err:=os.OpenRoot(directory);if err!=nil {return nil,err}
	return &workspaceFiles{root:r,directory:filepath.Clean(directory)},nil
}
func (w *workspaceFiles) Name() string {return "galaxy-account-workspace"}
func (w *workspaceFiles) ResolveFileIO(context.Context) fileio.FileIO {return w}
func (w *workspaceFiles) relative(name string) (string,error) {
	if name=="" || strings.ContainsAny(name,"\\\x00") {return "",fs.ErrPermission}
	if filepath.IsAbs(name) {
		rel,err:=filepath.Rel(w.directory,name);if err!=nil {return "",fs.ErrPermission};name=rel
	}
	for _,part:=range strings.Split(name,"/") {if part==".." {return "",fs.ErrPermission}}
	name=filepath.Clean(name)
	if !filepath.IsLocal(name) || name=="." {return "",fs.ErrPermission}
	current:=""
	for _,part:=range strings.Split(name,"/") {
		current=filepath.Join(current,part);info,err:=w.root.Lstat(current)
		if errors.Is(err,fs.ErrNotExist) {break};if err!=nil {return "",err}
		if info.Mode()&fs.ModeSymlink!=0 {return "",fs.ErrPermission}
	}
	return name,nil
}
func (w *workspaceFiles) Open(name string) (fileio.File,error) {
	rel,err:=w.relative(name);if err!=nil {return nil,err};f,err:=w.root.Open(rel)
	if err!=nil {return nil,err};info,err:=f.Stat()
	if err!=nil || !info.Mode().IsRegular() {f.Close();return nil,fs.ErrPermission};return f,nil
}
func (w *workspaceFiles) Stat(name string) (fileio.FileInfo,error) {
	rel,err:=w.relative(name);if err!=nil {return nil,err};return w.root.Stat(rel)
}
func (w *workspaceFiles) ResolvePath(name string) (string,error) {
	rel,err:=w.relative(name);if err!=nil {return "",err};return filepath.Join(w.directory,rel),nil
}
func (w *workspaceFiles) Save(name string, _ fileio.SaveOptions, body io.Reader) (fileio.SaveResult,error) {
	rel,err:=w.relative(name);if err!=nil {return nil,err}
	parent:=filepath.Dir(rel);if err=w.root.MkdirAll(parent,0700);err!=nil {return nil,err}
	var random [16]byte;if _,err=rand.Read(random[:]);err!=nil {return nil,err}
	tmp:=filepath.Join(parent,".galaxy-"+hex.EncodeToString(random[:]))
	f,err:=w.root.OpenFile(tmp,os.O_CREATE|os.O_EXCL|os.O_WRONLY,0600);if err!=nil {return nil,err}
	defer w.root.Remove(tmp)
	n,err:=io.Copy(f,body);if err==nil {err=f.Sync()};closeErr:=f.Close()
	if err!=nil {return nil,err};if closeErr!=nil {return nil,closeErr}
	if err=w.root.Rename(tmp,rel);err!=nil {return nil,err};return savedFile(n),nil
}
func (w *workspaceFiles) RemoveWorkspaceEntry(name string) error {
	rel,err:=w.relative(name);if err!=nil {return err};return w.root.Remove(rel)
}
