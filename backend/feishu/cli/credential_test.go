package main

import (
 "context"
 "testing"
 "github.com/larksuite/cli/extension/credential"
)

func TestPrivateProviderNeverMixesApplicationAndEmployeeTokens(t *testing.T) {
 for _,identity:=range []string{"user","application"} {
  p:=&provider{value:frame{AppID:"cli_test",AccessToken:"private-token",Identity:identity}}
  account,err:=p.ResolveAccount(context.Background());if err!=nil {t.Fatal(err)}
  want:=credential.TokenTypeUAT;other:=credential.TokenTypeTAT
  if identity=="application" {
   want,other=other,want
   if account.DefaultAs!=credential.IdentityBot || account.SupportedIdentities!=credential.SupportsBot {t.Fatal("application identity not isolated")}
  } else if account.DefaultAs!=credential.IdentityUser || account.SupportedIdentities!=credential.SupportsUser {t.Fatal("employee identity not isolated")}
  token,err:=p.ResolveToken(context.Background(),credential.TokenSpec{Type:want,AppID:"cli_test"})
  if err!=nil || token.Value!="private-token" {t.Fatal("expected identity rejected")}
  if _,err=p.ResolveToken(context.Background(),credential.TokenSpec{Type:other,AppID:"cli_test"});err==nil {t.Fatal("wrong token type accepted")}
  if _,err=p.ResolveToken(context.Background(),credential.TokenSpec{Type:want,AppID:"another_app"});err==nil {t.Fatal("another application accepted")}
 }
}
