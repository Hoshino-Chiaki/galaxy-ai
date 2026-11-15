// Export public upstream metadata without credentials or business API calls.
package main

import (
	"encoding/json"
	"os"

	"github.com/larksuite/cli/shortcuts"
	"github.com/larksuite/cli/shortcuts/common"
)

type entry struct {
	Service string `json:"service"`
	Command string `json:"command"`
	Description string `json:"description"`
	Risk string `json:"risk"`
	Scopes []string `json:"scopes"`
	ConditionalScopes []string `json:"conditional_scopes"`
	Identities []string `json:"identities"`
	Flags []common.Flag `json:"flags"`
	Schemas map[string]json.RawMessage `json:"schemas,omitempty"`
}

func main() {
	entries := []entry{}
	for _, s := range shortcuts.AllShortcuts() {
		if s.Hidden { continue }
		identities := s.AuthTypes
		if len(identities)==0 { identities=[]string{"user"} }
		e := entry{Service:s.Service,Command:s.Command,Description:s.Description,Risk:s.Risk,
			Scopes:s.ScopesForIdentity("user"), ConditionalScopes:s.ConditionalScopesForIdentity("user"),
			Identities:identities,Flags:s.Flags,Schemas:map[string]json.RawMessage{}}
		if s.PrintFlagSchema!=nil {
			for _, flag:= range s.Flags {
				if raw,err:=s.PrintFlagSchema(flag.Name); err==nil && json.Valid(raw) { e.Schemas[flag.Name]=raw }
			}
		}
		entries=append(entries,e)
	}
	if err:=json.NewEncoder(os.Stdout).Encode(entries);err!=nil { panic(err) }
}
