package main

import (
	"math"
	"testing"
)

func TestKernelUsesNextOpenAndNonOverlappingTrades(t *testing.T) {
	opens := []float64{100, 101, 102, 103, 104, 105, 106}
	input := request{
		Opens:   opens,
		Sources: [][]int{{0, 1, 3}},
		Recipes: []recipe{{Family: "single_1", Sources: []int{0}, Hold: 1, Side: 1}},
		Start:   0,
		End:     len(opens),
		Workers: 1,
	}
	got := run(input).Results[0]
	want := (102.0/101.0 - 1) + (105.0/104.0 - 1)
	if got.Trades != 2 || math.Abs(got.TotalReturn-want) > 1e-15 {
		t.Fatalf("got trades=%d total=%v, want trades=2 total=%v", got.Trades, got.TotalReturn, want)
	}
}

func TestCombineAllAndAny(t *testing.T) {
	sources := [][]int{{1, 3}, {2, 3}}
	all := combine("all_2", []int{0, 1}, sources)
	any := combine("any_2", []int{0, 1}, sources)
	if len(all) != 1 || all[0] != 3 {
		t.Fatalf("all=%v", all)
	}
	if len(any) != 3 || any[0] != 1 || any[1] != 2 || any[2] != 3 {
		t.Fatalf("any=%v", any)
	}
}
