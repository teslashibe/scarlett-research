package main

import (
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"sort"
	"sync"
)

type request struct {
	Opens   []float64 `json:"opens"`
	Sources [][]int   `json:"sources"`
	Recipes []recipe  `json:"recipes"`
	Start   int       `json:"start"`
	End     int       `json:"end"`
	CostBPS float64   `json:"costBps"`
	Workers int       `json:"workers"`
}

type recipe struct {
	Family  string `json:"family"`
	Sources []int  `json:"sources"`
	Hold    int    `json:"hold"`
	Side    int    `json:"side"`
}

type metrics struct {
	Trades      int      `json:"trades"`
	TotalReturn float64  `json:"totalReturn"`
	MeanReturn  *float64 `json:"meanReturn"`
	WinRate     *float64 `json:"winRate"`
	MaxDrawdown float64  `json:"maxDrawdown"`
}

type response struct {
	Results []metrics `json:"results"`
}

func combine(family string, sourceIDs []int, sources [][]int) []int {
	if len(sourceIDs) == 0 {
		return nil
	}
	if family == "single_1" {
		return sources[sourceIDs[0]]
	}
	if len(sourceIDs) == 2 {
		left, right := sources[sourceIDs[0]], sources[sourceIDs[1]]
		result := make([]int, 0, len(left)+len(right))
		for i, j := 0, 0; i < len(left) || j < len(right); {
			if i == len(left) {
				if family == "any_2" {
					result = append(result, right[j:]...)
				}
				break
			}
			if j == len(right) {
				if family == "any_2" {
					result = append(result, left[i:]...)
				}
				break
			}
			switch {
			case left[i] == right[j]:
				result = append(result, left[i])
				i++
				j++
			case left[i] < right[j]:
				if family == "any_2" {
					result = append(result, left[i])
				}
				i++
			default:
				if family == "any_2" {
					result = append(result, right[j])
				}
				j++
			}
		}
		return result
	}
	counts := make(map[int]int)
	for _, sourceID := range sourceIDs {
		for _, index := range sources[sourceID] {
			counts[index]++
		}
	}
	result := make([]int, 0, len(counts))
	for index, count := range counts {
		if family == "any_2" || count == len(sourceIDs) {
			result = append(result, index)
		}
	}
	// Source indices are chronological; map iteration is not.
	sort.Ints(result)
	return result
}

func evaluate(opens []float64, signal []int, item recipe, start, end int, costBPS float64) metrics {
	result := metrics{}
	curve, peak := 0.0, 0.0
	nextAllowed := start
	for _, index := range signal {
		if index < nextAllowed || index < start {
			continue
		}
		entry, exit := index+1, index+1+item.Hold
		if exit >= end || exit >= len(opens) {
			break
		}
		value := float64(item.Side)*(opens[exit]/opens[entry]-1) - costBPS/10_000
		result.Trades++
		result.TotalReturn += value
		if value > 0 {
			if result.WinRate == nil {
				zero := 0.0
				result.WinRate = &zero
			}
			*result.WinRate++
		}
		curve += value
		if curve > peak {
			peak = curve
		}
		if drawdown := peak - curve; drawdown > result.MaxDrawdown {
			result.MaxDrawdown = drawdown
		}
		nextAllowed = exit + 1
	}
	if result.Trades > 0 {
		mean := result.TotalReturn / float64(result.Trades)
		wins := 0.0
		if result.WinRate != nil {
			wins = *result.WinRate
		}
		winRate := wins / float64(result.Trades)
		result.MeanReturn = &mean
		result.WinRate = &winRate
	}
	return result
}

func run(input request) response {
	workers := input.Workers
	if workers <= 0 {
		workers = runtime.GOMAXPROCS(0)
	}
	results := make([]metrics, len(input.Recipes))
	jobs := make(chan int)
	var group sync.WaitGroup
	for range workers {
		group.Add(1)
		go func() {
			defer group.Done()
			for index := range jobs {
				item := input.Recipes[index]
				signal := combine(item.Family, item.Sources, input.Sources)
				results[index] = evaluate(
					input.Opens, signal, item, input.Start, input.End, input.CostBPS,
				)
			}
		}()
	}
	for index := range input.Recipes {
		jobs <- index
	}
	close(jobs)
	group.Wait()
	return response{Results: results}
}

func main() {
	decoder := json.NewDecoder(os.Stdin)
	var input request
	if err := decoder.Decode(&input); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if input.Start < 0 || input.End > len(input.Opens) || input.Start >= input.End {
		fmt.Fprintln(os.Stderr, "invalid partition")
		os.Exit(2)
	}
	if err := json.NewEncoder(os.Stdout).Encode(run(input)); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
}
