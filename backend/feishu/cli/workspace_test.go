package main

import (
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/larksuite/cli/extension/fileio"
)

func TestWorkspaceFilesStayInsideEmployeeDirectory(t *testing.T) {
	dir:=t.TempDir();outside:=t.TempDir()
	if err:=os.WriteFile(filepath.Join(outside,"secret"),[]byte("another account"),0600);err!=nil {t.Fatal(err)}
	w,err:=newWorkspaceFiles(dir);if err!=nil {t.Fatal(err)};defer w.root.Close()
	if err=os.Symlink(outside,filepath.Join(dir,"link"));err!=nil {t.Fatal(err)}
	for _,name:=range []string{"../secret",filepath.Join(outside,"secret"),"link/secret","nested/../../secret"} {
		if f,err:=w.Open(name);err==nil {f.Close();t.Fatalf("read escaped: %s",name)}
		if _,err:=w.Save(name,fileio.SaveOptions{},strings.NewReader("overwrite"));err==nil {t.Fatalf("write escaped: %s",name)}
	}
	result,err:=w.Save("reports/test.txt",fileio.SaveOptions{},strings.NewReader("own data"));if err!=nil {t.Fatal(err)}
	if result.Size()!=8 {t.Fatal(result.Size())}
	f,err:=w.Open("reports/test.txt");if err!=nil {t.Fatal(err)};defer f.Close()
	data,err:=io.ReadAll(f);if err!=nil || string(data)!="own data" {t.Fatalf("%q %v",data,err)}
	data,err=os.ReadFile(filepath.Join(outside,"secret"));if err!=nil || string(data)!="another account" {t.Fatal("outside changed")}
}

func TestWorkspaceSaveFailureLeavesNoPartialFile(t *testing.T) {
	w,err:=newWorkspaceFiles(t.TempDir());if err!=nil {t.Fatal(err)};defer w.root.Close()
	if _,err=w.Save("test.txt",fileio.SaveOptions{},strings.NewReader("first"));err!=nil {t.Fatal(err)}
	if _,err=w.Save("test.txt",fileio.SaveOptions{},strings.NewReader("second"));err!=nil {t.Fatal(err)}
	f,err:=w.Open("test.txt");if err!=nil {t.Fatal(err)};defer f.Close()
	data,_:=io.ReadAll(f);if string(data)!="second" {t.Fatal(string(data))}
}
