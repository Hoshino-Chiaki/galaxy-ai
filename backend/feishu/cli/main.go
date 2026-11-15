// Galaxy AI embeds the official CLI without exposing shell or account management.
package main

import (
 "context"
 _ "embed"
 "encoding/json"
 "io"
 "os"
 "strings"

 "github.com/larksuite/cli/cmd"
 "github.com/larksuite/cli/extension/credential"
 "github.com/larksuite/cli/extension/platform"
 "github.com/larksuite/cli/extension/fileio"
)

//go:embed allowed-commands.json
var allowedJSON []byte

type frame struct {
 AppID string `json:"app_id"`
 AccessToken string `json:"access_token"`
 OpenID string `json:"open_id"`
 Scopes string `json:"scopes"`
 Identity string `json:"identity"`
 Workspace string `json:"workspace_directory"`
}
type provider struct { value frame }
func (p *provider) Name() string { return "galaxy-private-pipe" }
func (p *provider) Priority() int { return -100 }
func (p *provider) ResolveAccount(context.Context) (*credential.Account,error) {
 if p.value.AppID=="" || p.value.AccessToken=="" { return nil,&credential.BlockError{Provider:p.Name(),Reason:"employee authorization unavailable"} }
 identity:=credential.IdentityUser;support:=credential.SupportsUser
 if p.value.Identity=="application" {identity=credential.IdentityBot;support=credential.SupportsBot}
 return &credential.Account{AppID:p.value.AppID, Brand:credential.BrandFeishu, DefaultAs:identity,
  OpenID:p.value.OpenID, SupportedIdentities:support},nil
}
func (p *provider) ResolveToken(_ context.Context, spec credential.TokenSpec) (*credential.Token,error) {
 expected:=credential.TokenTypeUAT
 if p.value.Identity=="application" {expected=credential.TokenTypeTAT}
 if spec.Type!=expected || spec.AppID!=p.value.AppID { return nil,&credential.BlockError{Provider:p.Name(),Reason:"identity denied"} }
 return &credential.Token{Value:p.value.AccessToken,Scopes:p.value.Scopes,Source:p.Name()},nil
}
func fail() { _,_ = os.Stderr.WriteString(`{"ok":false,"error":{"type":"policy","message":"Galaxy Feishu command or context denied"}}`+"\n");os.Exit(2) }
func main() {
 args:=os.Args[1:]
 // Only paths exported from the pinned business catalog can be reached.
 var paths []string
 if json.Unmarshal(allowedJSON,&paths)!=nil {fail()}
 end:=0;for end<len(args) && !strings.HasPrefix(args[end],"--") {end++}
 path:=strings.Join(args[:end],"/")
 allowed:=false;for _,p:=range paths {if path==p {allowed=true;break}}
 if !allowed { fail() }
 var value frame
 raw,err:=io.ReadAll(io.LimitReader(os.Stdin,32769));if err!=nil || len(raw)>32768 || json.Unmarshal(raw,&value)!=nil {fail()}
 if (value.Identity=="application") != (path=="attendance/user_tasks/query") {fail()}
 credential.Register(&provider{value:value})
 files,err:=newWorkspaceFiles(value.Workspace);if err!=nil {fail()};defer files.root.Close()
 fileio.Register(files)
 platform.Register(platform.NewPlugin("galaxy-feishu-business","0.2.0").Restrict(&platform.Rule{
  Name:"galaxy-feishu-business",Allow:paths,MaxRisk:platform.RiskHighRiskWrite,
 }).MustBuild())
 os.Exit(cmd.Execute())
}
