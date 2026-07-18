/* YSL Competition Engine
   Shared by admin, fixtures and standings.
   Stores all structures inside the existing /api/site JSON. */
(function(global){
  "use strict";

  const Engine = {};
  const completeStatuses = new Set(["complete","completed","finished","full-time","full time"]);

  function uid(){
    return global.crypto?.randomUUID ? global.crypto.randomUUID() :
      `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
  function number(value, fallback=0){
    const n=Number(value);
    return Number.isFinite(n)?n:fallback;
  }
  function isComplete(fixture){
    return completeStatuses.has(String(fixture?.status||"").toLowerCase());
  }
  function competitionKey(c){ return c?.id||c?.key||c?.name||""; }
  function stageById(c,id){ return (c?.stages||[]).find(s=>String(s.id)===String(id)); }
  function tieById(c,id){
    for(const stage of c?.stages||[]){
      const tie=(stage.ties||[]).find(t=>String(t.id)===String(id));
      if(tie)return {stage,tie};
    }
    return null;
  }
  function teamLogo(data,name){
    return (data.teams||[]).find(t=>t.name===name)?.logo||"";
  }
  function normaliseSource(source,fallbackTeam=""){
    if(source&&typeof source==="object") return {
      type:source.type||"team",
      team:source.team||"",
      tieId:source.tieId||"",
      competitionId:source.competitionId||"",
      stageId:source.stageId||"",
      groupId:source.groupId||"",
      position:number(source.position,1)
    };
    return {type:"team",team:fallbackTeam||"",tieId:"",competitionId:"",stageId:"",groupId:"",position:1};
  }

  Engine.normalise = function(data){
    data=data&&typeof data==="object"?data:{};
    data.teams=Array.isArray(data.teams)?data.teams:[];
    data.fixtures=Array.isArray(data.fixtures)?data.fixtures:[];
    data.competitions=Array.isArray(data.competitions)?data.competitions:[];
    data.competitions.forEach((c,ci)=>{
      c.id=c.id||c.key||uid();
      c.name=c.name||`Competition ${ci+1}`;
      c.key=c.key||`competition-${ci+1}`;
      c.enabled=c.enabled!==false;
      c.stages=Array.isArray(c.stages)?c.stages:[];
      c.stages.forEach((s,si)=>{
        s.id=s.id||uid(); s.name=s.name||`Stage ${si+1}`; s.type=s.type||"league"; s.order=number(s.order,si+1);
        s.groups=Array.isArray(s.groups)?s.groups:[];
        s.ties=Array.isArray(s.ties)?s.ties:[];
        s.rules={
          roundRobin:"single",qualify:0,relegate:0,legs:1,aggregate:false,awayGoals:false,
          extraTime:true,penalties:true,thirdPlace:false,autoProgress:false,
          scheduleDate:"",scheduleTime:"",intervalMinutes:60,...(s.rules||{})
        };
        s.rules.legs=Math.max(1,number(s.rules.legs,1));
        s.rules.intervalMinutes=Math.max(0,number(s.rules.intervalMinutes,60));
        s.groups.forEach((g,gi)=>{
          g.id=g.id||uid();g.name=g.name||`Group ${String.fromCharCode(65+gi)}`;
          g.teams=Array.isArray(g.teams)?g.teams:[];g.qualify=number(g.qualify,2);
        });
        s.ties.forEach((t,ti)=>{
          t.id=t.id||uid();t.name=t.name||`Tie ${ti+1}`;
          t.homeSource=normaliseSource(t.homeSource,t.home||"");
          t.awaySource=normaliseSource(t.awaySource,t.away||"");
          t.home=t.home||t.homeSource.team||"";
          t.away=t.away||t.awaySource.team||"";
          t.nextTieId=t.nextTieId||"";
          t.nextTieSlot=t.nextTieSlot==="away"?"away":"home";
        });
      });
    });
    data.fixtures.forEach(f=>{
      f.id=f.id||uid();f.competition=f.competition||"";
      f.competitionId=f.competitionId||"";
      f.stageId=f.stageId||"";f.groupId=f.groupId||"";f.tieId=f.tieId||"";
      f.legNumber=Math.max(1,number(f.legNumber,1));
      f.home=f.home||"";f.away=f.away||"";
      f.homeLogo=f.homeLogo||teamLogo(data,f.home);f.awayLogo=f.awayLogo||teamLogo(data,f.away);
    });
    return data;
  };

  Engine.calculateTable = function(data,competitionId,stageId,groupId=""){
    Engine.normalise(data);
    const competition=data.competitions.find(c=>String(c.id)===String(competitionId)||c.name===competitionId);
    const stage=stageById(competition,stageId);
    let teamNames=[];
    if(groupId){
      teamNames=(stage?.groups||[]).find(g=>String(g.id)===String(groupId))?.teams||[];
    }else if(stage?.type==="groups"){
      teamNames=[...new Set((stage.groups||[]).flatMap(g=>g.teams||[]))];
    }else{
      teamNames=(stage?.teams||[]).length?stage.teams:data.teams.map(t=>t.name);
    }
    const table=new Map(teamNames.filter(Boolean).map(name=>[name,{
      name,logo:teamLogo(data,name),played:0,wins:0,draws:0,losses:0,gf:0,ga:0,gd:0,points:0,form:[]
    }]));
    const fixtures=data.fixtures
      .filter(f=>(!competition||f.competitionId===competition.id||f.competition===competition.name))
      .filter(f=>!stageId||String(f.stageId)===String(stageId))
      .filter(f=>!groupId||String(f.groupId)===String(groupId))
      .filter(isComplete)
      .sort((a,b)=>`${a.date||""} ${a.time||""}`.localeCompare(`${b.date||""} ${b.time||""}`));
    for(const f of fixtures){
      if(!table.has(f.home))table.set(f.home,{name:f.home,logo:teamLogo(data,f.home),played:0,wins:0,draws:0,losses:0,gf:0,ga:0,gd:0,points:0,form:[]});
      if(!table.has(f.away))table.set(f.away,{name:f.away,logo:teamLogo(data,f.away),played:0,wins:0,draws:0,losses:0,gf:0,ga:0,gd:0,points:0,form:[]});
      if(!f.home||!f.away)continue;
      const h=table.get(f.home),a=table.get(f.away),hs=number(f.homeScore),as=number(f.awayScore);
      h.played++;a.played++;h.gf+=hs;h.ga+=as;a.gf+=as;a.ga+=hs;
      if(hs>as){h.wins++;a.losses++;h.points+=3;h.form.push("W");a.form.push("L")}
      else if(hs<as){a.wins++;h.losses++;a.points+=3;a.form.push("W");h.form.push("L")}
      else{h.draws++;a.draws++;h.points++;a.points++;h.form.push("D");a.form.push("D")}
    }
    return [...table.values()].map(t=>({...t,gd:t.gf-t.ga,form:t.form.slice(-5)}))
      .sort((a,b)=>b.points-a.points||b.gd-a.gd||b.gf-a.gf||a.name.localeCompare(b.name))
      .map((t,i)=>({...t,position:i+1}));
  };

  Engine.tieResult = function(data,competition,tieId){
    const found=tieById(competition,tieId);
    if(!found)return null;
    const {stage,tie}=found;
    const fixtures=data.fixtures.filter(f=>
      String(f.tieId)===String(tie.id) &&
      (f.competitionId===competition.id||f.competition===competition.name)
    ).sort((a,b)=>number(a.legNumber,1)-number(b.legNumber,1));
    const required=Math.max(1,number(stage.rules?.legs,1));
    const completed=fixtures.filter(isComplete);
    if(completed.length<required)return {tie,stage,fixtures,complete:false,winner:"",aggregateHome:0,aggregateAway:0};

    const homeTeam=tie.home||tie.homeSource?.team||fixtures[0]?.home||"";
    const awayTeam=tie.away||tie.awaySource?.team||fixtures[0]?.away||"";
    let homeAgg=0,awayAgg=0,homeAwayGoals=0,awayAwayGoals=0;
    for(const f of completed.slice(0,required)){
      const hs=number(f.homeScore),as=number(f.awayScore);
      if(f.home===homeTeam){homeAgg+=hs;awayAgg+=as;awayAwayGoals+=as}
      else if(f.away===homeTeam){homeAgg+=as;awayAgg+=hs;homeAwayGoals+=as}
      else {homeAgg+=hs;awayAgg+=as}
    }
    let winner="";
    if(homeAgg>awayAgg)winner=homeTeam;
    else if(awayAgg>homeAgg)winner=awayTeam;
    else if(stage.rules?.awayGoals){
      if(homeAwayGoals>awayAwayGoals)winner=homeTeam;
      else if(awayAwayGoals>homeAwayGoals)winner=awayTeam;
    }
    if(!winner){
      const last=completed.at(-1);
      const hp=number(last.homePenalties??last.home_penalties,-1);
      const ap=number(last.awayPenalties??last.away_penalties,-1);
      if(hp>=0&&ap>=0&&hp!==ap)winner=hp>ap?last.home:last.away;
    }
    return {tie,stage,fixtures,complete:Boolean(winner),winner,aggregateHome:homeAgg,aggregateAway:awayAgg};
  };

  Engine.resolveSource = function(data,competition,source){
    source=normaliseSource(source);
    if(source.type==="team")return source.team||"";
    if(source.type==="winner")return Engine.tieResult(data,competition,source.tieId)?.winner||"";
    if(source.type==="loser"){
      const result=Engine.tieResult(data,competition,source.tieId);
      if(!result?.winner)return "";
      const teams=[result.tie.home,result.tie.away].filter(Boolean);
      return teams.find(t=>t!==result.winner)||"";
    }
    if(source.type==="group-position"){
      const table=Engine.calculateTable(data,source.competitionId||competition.id,source.stageId,source.groupId);
      return table[Math.max(0,number(source.position,1)-1)]?.name||"";
    }
    return "";
  };

  function updateFixtureTeams(data,competition,tie){
    const home=Engine.resolveSource(data,competition,tie.homeSource)||tie.home||"";
    const away=Engine.resolveSource(data,competition,tie.awaySource)||tie.away||"";
    tie.home=home;tie.away=away;
    data.fixtures.filter(f=>String(f.tieId)===String(tie.id)).forEach((f,index)=>{
      const reverse=number(f.legNumber,1)%2===0;
      f.home=reverse?away:home;f.away=reverse?home:away;
      f.homeLogo=teamLogo(data,f.home);f.awayLogo=teamLogo(data,f.away);
    });
  }

  Engine.process = function(data){
    Engine.normalise(data);
    for(const competition of data.competitions){
      const ordered=[...(competition.stages||[])].sort((a,b)=>a.order-b.order);
      for(const stage of ordered){
        for(const tie of stage.ties||[])updateFixtureTeams(data,competition,tie);
      }
      // Repeat so a semi-final winner can populate a final in the same run.
      for(let pass=0;pass<4;pass++){
        for(const stage of ordered){
          for(const tie of stage.ties||[]){
            const result=Engine.tieResult(data,competition,tie.id);
            if(result?.winner&&tie.nextTieId){
              const target=tieById(competition,tie.nextTieId)?.tie;
              if(target){
                const slot=tie.nextTieSlot||(!target.homeSource?.tieId&&!target.home?"home":"away");
                target[`${slot}Source`]={type:"winner",tieId:tie.id,team:"",competitionId:competition.id,stageId:stage.id,groupId:"",position:1};
                updateFixtureTeams(data,competition,target);
              }
            }
            if(result){
              result.fixtures.forEach(f=>{f.aggregateHome=result.aggregateHome;f.aggregateAway=result.aggregateAway});
            }
          }
        }
      }
    }
    return data;
  };

  function addMinutes(date,time,minutes){
    if(!date)return {date:"",time:""};
    const start=new Date(`${date}T${time||"00:00"}:00`);
    if(Number.isNaN(start.getTime()))return {date,time};
    start.setMinutes(start.getMinutes()+minutes);
    const pad=n=>String(n).padStart(2,"0");
    return {
      date:`${start.getFullYear()}-${pad(start.getMonth()+1)}-${pad(start.getDate())}`,
      time:`${pad(start.getHours())}:${pad(start.getMinutes())}`
    };
  }

  Engine.generateKnockoutFixtures = function(data,competitionId,{replace=false}={}){
    Engine.normalise(data);
    const competition=data.competitions.find(c=>String(c.id)===String(competitionId));
    if(!competition)throw new Error("Competition was not found.");
    if(replace){
      const tieIds=new Set(competition.stages.flatMap(s=>(s.ties||[]).map(t=>String(t.id))));
      data.fixtures=data.fixtures.filter(f=>!tieIds.has(String(f.tieId)));
    }
    let created=0;
    const ordered=[...competition.stages].sort((a,b)=>a.order-b.order);
    let globalOffset=0;
    for(const stage of ordered){
      if(!["knockout","playoff","custom"].includes(stage.type)||!(stage.ties||[]).length)continue;
      const legs=Math.max(1,number(stage.rules?.legs,1));
      const date=stage.rules?.scheduleDate||"";
      const time=stage.rules?.scheduleTime||"";
      const interval=Math.max(0,number(stage.rules?.intervalMinutes,60));
      for(const tie of stage.ties){
        updateFixtureTeams(data,competition,tie);
        for(let leg=1;leg<=legs;leg++){
          const exists=data.fixtures.some(f=>String(f.tieId)===String(tie.id)&&number(f.legNumber,1)===leg);
          if(exists)continue;
          const when=addMinutes(date,time,globalOffset*interval);
          const reverse=leg%2===0;
          const home=reverse?tie.away:tie.home;
          const away=reverse?tie.home:tie.away;
          data.fixtures.push({
            id:uid(),
            competition:competition.name,
            competitionId:competition.id,
            stageId:stage.id,
            groupId:"",
            tieId:tie.id,
            legNumber:leg,
            matchday:"",
            round:"",
            date:when.date,
            time:when.time,
            home:home||"",
            away:away||"",
            homeLogo:teamLogo(data,home),
            awayLogo:teamLogo(data,away),
            venue:"",
            status:"upcoming",
            homeScore:"",
            awayScore:"",
            streamUrl:"",
            stats:{goals:[],cards:[],substitutions:[],lineups:{home:[],away:[]}}
          });
          created++;globalOffset++;
        }
      }
    }
    Engine.process(data);
    return created;
  };

  Engine.generateRoundRobinFixtures = function(data,competitionId,stageId,{replace=false}={}){
    Engine.normalise(data);
    const competition=data.competitions.find(c=>String(c.id)===String(competitionId));
    const stage=stageById(competition,stageId);
    if(!competition||!stage)throw new Error("Stage was not found.");
    const groups=stage.type==="groups"&&stage.groups.length?stage.groups:[{id:"",name:"",teams:(stage.teams||[]).length?stage.teams:data.teams.map(t=>t.name)}];
    if(replace)data.fixtures=data.fixtures.filter(f=>!(f.competitionId===competition.id&&f.stageId===stage.id));
    let created=0,offset=0;
    for(const group of groups){
      let teams=[...(group.teams||[])].filter(Boolean);
      if(teams.length<2)continue;
      if(teams.length%2)teams.push("");
      const rounds=teams.length-1,half=teams.length/2;
      const passes=stage.rules?.roundRobin==="double"?2:1;
      let rotation=[...teams];
      for(let pass=0;pass<passes;pass++){
        for(let round=0;round<rounds;round++){
          for(let i=0;i<half;i++){
            let home=rotation[i],away=rotation[rotation.length-1-i];
            if(pass===1)[home,away]=[away,home];
            if(!home||!away)continue;
            const exists=data.fixtures.some(f=>f.competitionId===competition.id&&f.stageId===stage.id&&f.groupId===(group.id||"")&&f.home===home&&f.away===away);
            if(exists)continue;
            const when=addMinutes(stage.rules?.scheduleDate||"",stage.rules?.scheduleTime||"",offset*number(stage.rules?.intervalMinutes,60));
            data.fixtures.push({
              id:uid(),competition:competition.name,competitionId:competition.id,stageId:stage.id,groupId:group.id||"",tieId:"",
              legNumber:1,matchday:round+1+(pass*rounds),round:group.name||"",date:when.date,time:when.time,
              home,away,homeLogo:teamLogo(data,home),awayLogo:teamLogo(data,away),venue:"",status:"upcoming",
              homeScore:"",awayScore:"",streamUrl:"",stats:{goals:[],cards:[],substitutions:[],lineups:{home:[],away:[]}}
            });
            created++;offset++;
          }
          rotation=[rotation[0],rotation.at(-1),...rotation.slice(1,-1)];
        }
      }
    }
    return created;
  };

  Engine.stageName = function(data,fixture){
    const c=data.competitions.find(x=>x.id===fixture.competitionId||x.name===fixture.competition);
    return stageById(c,fixture.stageId)?.name||fixture.stage||fixture.round||"";
  };

  global.YSLCompetitionEngine=Engine;
})(window);
