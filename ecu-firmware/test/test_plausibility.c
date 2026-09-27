#include <stdio.h>
#include <stdint.h>
#include "plausibility.h"
#include "sensor_defs.h"

/* raw ADC code a channel produces for a given sensor output voltage */
static uint16_t code(unsigned mv){ return (uint16_t)((4096.0*mv*0.5)/3300.0 + 0.5); }
/* the code each pedal track produces at a given travel percent */
static uint16_t app1_at(double pct){ return code(500 + 4000*pct/100.0); }
static uint16_t app2_at(double pct){ return code(500 + 2000*pct/100.0); }
static uint16_t tps1_at(double pct){ return code(500 + 4000*pct/100.0); }
static uint16_t tps2_at(double pct){ return code(4500 - 4000*pct/100.0); }

static const char* nm(plaus_result_t r){
    return r==PLAUS_OK?"OK":r==PLAUS_FAULT_RANGE?"RANGE":"DISAGREE"; }

static int fails;

/* `expect` is "-" for a pair that must stay clean, or "fault" for one
 * that must trip. Asserting rather than just printing matters: an
 * earlier version of this file always returned success, which made it
 * exactly the kind of check-that-cannot-fail it exists to test for. */
static void scenario(const char*what, uint16_t a1,uint16_t a2,uint16_t t1,uint16_t t2,
                     const char*expect){
    uint16_t raw[SENSOR_COUNT]={0};
    raw[SENSOR_APP1]=a1; raw[SENSOR_APP2]=a2;
    raw[SENSOR_TPS1]=t1; raw[SENSOR_TPS2]=t2;
    plausibility_clear_latched();
    plausibility_update(raw);
    const char*app=nm(plausibility_pair_state(0));
    const char*tps=nm(plausibility_pair_state(1));
    int ok = (expect[0]=='-') ? (!plausibility_latched())
                              : (plausibility_latched()!=0);
    printf("  %-38s app=%-8s tps=%-8s  %s\n", what, app, tps, ok?"":"<-- UNEXPECTED");
}

int main(void){
    printf("== healthy: both tracks agree across the travel ==\n");
    for(double pct=0; pct<=100; pct+=25)
        scenario("pedal & throttle healthy", app1_at(pct),app2_at(pct),
                 tps1_at(pct),tps2_at(pct), "-");

    printf("\n== the failures a SINGLE channel cannot reveal ==\n");
    scenario("APP2 stuck at 30%% while pedal moves to 80%%",
             app1_at(80), app2_at(30), tps1_at(0), tps2_at(0), "fault");
    scenario("TPS2 stuck mid-travel, plate actually closed",
             app1_at(0), app2_at(0), tps1_at(0), tps2_at(50), "fault");

    printf("\n== the failure the OLD identical config could not catch ==\n");
    /* Cross-connected harness: both pedal pins carry track 1's signal.
     * With identical tracks the two channels would read the same and
     * agree perfectly. With a half-slope second track they cannot. */
    for(double pct=20; pct<=80; pct+=30){
        char buf[64]; snprintf(buf,sizeof buf,"APP harness cross-wired at %.0f%% travel",pct);
        scenario(buf, app1_at(pct), app1_at(pct), tps1_at(0), tps2_at(0), "fault");
    }

    printf("\n== broken wire / shorts: out of band, not a value ==\n");
    scenario("APP2 wire cut (pin pulled to 0 V)", app1_at(50), 0, tps1_at(0),tps2_at(0), "fault");
    scenario("APP1 shorted to 5 V rail",          4095, app2_at(50), tps1_at(0),tps2_at(0), "fault");
    scenario("TPS1 shorted to ground",            app1_at(0),app2_at(0), 0, tps2_at(0), "fault");

    printf("\n== tolerance: small mismatch allowed, large is not ==\n");
    printf("   tolerance = %d (0.01%%), i.e. %.1f percentage points\n",
           REDUNDANT_PAIRS[0].tolerance, REDUNDANT_PAIRS[0].tolerance/100.0);
    for(double err=2; err<=8; err+=3){
        char buf[64]; snprintf(buf,sizeof buf,"APP2 reading %.0f points high",err);
        scenario(buf, app1_at(50), app2_at(50+err), tps1_at(0),tps2_at(0),
                 err<=5?"-":"fault");
    }

    printf("\n== latching: a single bad sweep stays visible ==\n");
    uint16_t good[SENSOR_COUNT]={0};
    good[SENSOR_APP1]=app1_at(40); good[SENSOR_APP2]=app2_at(40);
    good[SENSOR_TPS1]=tps1_at(40); good[SENSOR_TPS2]=tps2_at(40);
    plausibility_clear_latched();
    for(int i=0;i<50;i++) plausibility_update(good);
    printf("  50 clean sweeps            -> latched=0x%lx\n",(unsigned long)plausibility_latched());
    uint16_t bad[SENSOR_COUNT]; for(int k=0;k<SENSOR_COUNT;k++) bad[k]=good[k];
    bad[SENSOR_APP2]=app2_at(90);
    plausibility_update(bad);
    for(int i=0;i<50;i++) plausibility_update(good);
    printf("  1 bad sweep, then 50 clean -> latched=0x%lx, live state now %s\n",
           (unsigned long)plausibility_latched(), nm(plausibility_pair_state(0)));
    if(plausibility_latched()==0) { fails++; printf("  FAIL: latch did not hold\n"); }
    if(plausibility_pair_state(0)!=PLAUS_OK){ fails++; printf("  FAIL: live state stuck\n"); }

    printf("plausibility: %s\n", fails? "FAILURES ABOVE":"all clean");
    return fails!=0;
}
